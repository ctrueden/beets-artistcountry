from beets.plugins import BeetsPlugin
from beets import ui, config
from beets.dbcore import types
from musicbrainzngs.musicbrainz import get_artist_by_id, get_area_by_id
import json
import os
from datetime import datetime


class CountryPlugin(BeetsPlugin):
    item_types = {'artist_country': types.STRING}

    def __init__(self):
        super().__init__()
        self.cache_file = os.path.join(config.config_dir(), 'artistcountry.json')
        self._cache = None

    def commands(self):
        def artistcountry_func(lib, opts, args):
            """Populate artist_country for items matching the query."""
            query = args if args else []
            items = lib.items(query)

            self._log.info(f'Processing {len(items)} items...')
            updated = 0

            for item in items:
                # Check if artist_country is already set in flexible attributes
                if not item._values_flex.get('artist_country'):
                    country = self.get_artist_country(item['mb_artistid'])
                    if country:
                        item['artist_country'] = country
                        item.store()
                        updated += 1
                        self._log.debug(f'Set artist_country={country} for {item.artist} - {item.title}')

            self._log.info(f'Updated {updated} items with artist_country')

        artistcountry_cmd = ui.Subcommand('artistcountry', help='populate artist_country fields')
        artistcountry_cmd.func = artistcountry_func
        return [artistcountry_cmd]

    def load_cache(self):
        """Load artist country cache from JSON file."""
        if self._cache is not None:
            return self._cache

        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    self._cache = json.load(f)
            else:
                self._cache = {}
        except (json.JSONDecodeError, IOError) as e:
            self._log.warning(f'Error loading artist cache: {e}')
            self._cache = {}

        return self._cache

    def save_cache(self):
        """Save artist country cache to JSON file."""
        if self._cache is None:
            return

        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

            with open(self.cache_file, 'w') as f:
                json.dump(self._cache, f, indent=2)
        except IOError as e:
            self._log.warning(f'Error saving artist cache: {e}')

    def get_artist_country(self, mb_artistid):
        """Get artist country, using cache first, then MusicBrainz."""
        if not mb_artistid or len(mb_artistid) != 36 or mb_artistid.count('-') != 4:
            return ''

        cache = self.load_cache()

        # Check cache first
        if mb_artistid in cache:
            return cache[mb_artistid].get('country', '')

        # Query MusicBrainz
        try:
            artist_item = get_artist_by_id(mb_artistid)
            artist = artist_item['artist']
            country = artist.get('country', '')

            if not country and 'area' in artist:
                try:
                    country = _country_from_area(artist['area'])
                except Exception:
                    self._log.debug(f"No country from area for artist {mb_artistid}")

            # Cache the result (even if empty)
            cache[mb_artistid] = {
                'country': country.lower() if country else '',
                'cached': datetime.now().isoformat(),
                'name': artist.get('name', '')
            }
            self._cache = cache
            self.save_cache()

            return country.lower() if country else ''

        except Exception as e:
            self._log.debug(f"Error fetching country for artist {mb_artistid}: {e}")
            # Cache the failure to avoid repeated API calls
            cache[mb_artistid] = {
                'country': '',
                'cached': datetime.now().isoformat(),
                'error': str(e)
            }
            self._cache = cache
            self.save_cache()
            return ''


# Global plugin instance for template function access
_plugin_instance = None

def get_plugin_instance():
    global _plugin_instance
    if _plugin_instance is None:
        _plugin_instance = CountryPlugin()
    return _plugin_instance


@CountryPlugin.template_field('artist_country')
def _tmpl_country(item):
    """Template field that returns artist_country, caching and storing result."""
    # Check if already stored in flexible attributes
    if item._values_flex.get('artist_country'):
        return item._values_flex['artist_country']

    # Get from cache/API
    plugin = get_plugin_instance()
    country = plugin.get_artist_country(item['mb_artistid'])

    # Store in flexible attributes for persistence
    if country:
        item['artist_country'] = country
        # Note: We don't call item.store() here to avoid side effects during template rendering

    return country


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
