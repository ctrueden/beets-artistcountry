import json
import os
from datetime import datetime

import beets
import musicbrainzngs
from beets import config, logging, ui
from beets.dbcore import types
from beets.plugins import BeetsPlugin
from musicbrainzngs.musicbrainz import get_artist_by_id, get_area_by_id

log = logging.getLogger('beets.artistcountry')


class CountryPlugin(BeetsPlugin):
    item_types = {'artist_country': types.STRING}

    def __init__(self):
        super(CountryPlugin, self).__init__()
        self.config.add({'auto': True})
        # beets 2.x no longer sets a user agent for musicbrainzngs, which
        # refuses to send requests without one.
        musicbrainzngs.set_useragent('beets', beets.__version__,
                                     'https://beets.io/')
        if self.config['auto']:
            self.import_stages = [self.imported]

    def commands(self):
        cmd = ui.Subcommand('artistcountry',
                            help=u'fetch the country of artists from MusicBrainz')
        cmd.parser.add_option(
            '-f', '--force', action='store_true', default=False,
            help=u'fetch the country again when it is already set')

        def func(lib, opts, args):
            for item in lib.items(args):
                if opts.force or not item.get('artist_country'):
                    fetch_country(item, refresh=opts.force)

        cmd.func = func
        return [cmd]

    def imported(self, session, task):
        for item in task.imported_items():
            fetch_country(item)


def fetch_country(item, refresh=False):
    country = _artist_country(item, refresh)
    if country:
        item['artist_country'] = country
        item.store()


class ArtistCache(object):
    """Countries of artists, keyed by MusicBrainz ID, saved to a JSON file
    so that each artist is looked up at most once across beets runs.
    """

    def __init__(self):
        self._entries = None

    @property
    def path(self):
        return os.path.join(config.config_dir(), 'artistcountry.json')

    def _load(self):
        if self._entries is None:
            try:
                with open(self.path) as f:
                    self._entries = json.load(f)
            except FileNotFoundError:
                self._entries = {}
            except (ValueError, OSError) as e:
                log.warning(u'Error loading artist cache {0}: {1}',
                            self.path, e)
                self._entries = {}
        return self._entries

    def get(self, artist_id):
        entry = self._load().get(artist_id)
        return None if entry is None else entry.get('country', '')

    def put(self, artist_id, country, name):
        self._load()[artist_id] = {
            'country': country,
            'name': name,
            'cached': datetime.now().isoformat(),
        }
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'w') as f:
                json.dump(self._entries, f, indent=2)
        except OSError as e:
            log.warning(u'Error saving artist cache {0}: {1}', self.path, e)


_cache = ArtistCache()


def _artist_country(item, refresh=False):
    mb_albumartistid = item['mb_albumartistid']

    # Check if we have a valid MusicBrainz UUID (not Spotify ID)
    if (not mb_albumartistid or len(mb_albumartistid) != 36
            or mb_albumartistid.count('-') != 4):
        return ''

    if not refresh:
        country = _cache.get(mb_albumartistid)
        if country is not None:
            return country

    try:
        artist_item = get_artist_by_id(mb_albumartistid)
        artist = artist_item['artist']
        country = artist.get('country', '')
        if not country:
            try:
                country = _country_from_area(artist['area'])
            except (KeyError, IndexError):
                log.info(u'No country for {0}', artist['name'])
                country = ''
        country = country.lower()
        if country:
            log.info(u'{0}: {1}', artist['name'], country)
        # Remember artists without a country too, so they are not refetched.
        _cache.put(mb_albumartistid, country, artist.get('name', ''))
        return country
    except musicbrainzngs.WebServiceError as e:
        # Not cached: network errors and the like may be transient.
        log.warning(u'Error fetching country for artist {0}: {1}',
                    mb_albumartistid, e)
        return ''


def _country_from_area(area):
    countries = _find_top_area(area)
    return countries[0]


def _find_top_area(area):
    new_area = get_area_by_id(area['id'], includes=['area-rels'])
    new_area = [
        a['area'] for a in new_area['area']['area-relation-list']
        if a.get('direction', '') == 'backward'
    ]

    if not new_area:
        return area['iso-3166-1-code-list']

    area = new_area[0]
    if _has_country_iso_code(area):
        return area['iso-3166-1-code-list']

    return _find_top_area(area)


def _has_country_iso_code(area):
    return area['type'] == "Country" and "iso-3166-1-code-list" in area
