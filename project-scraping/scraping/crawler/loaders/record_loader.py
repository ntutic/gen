from copy import deepcopy

from itemloaders.processors import Identity, Join, MapCompose, TakeFirst
from itemloaders.utils import arg_to_iter
from scrapy.loader import ItemLoader

from ..items import RecordFields, RecordItem
from ..processors import absolute_url, clean_features, float_clean, str_clean


def source_payload(item):
    """Retain extraction values, including explicit edits made after load_item."""
    raw = deepcopy(item.get("raw_payload", item))
    if "_loader_values" not in raw:
        return raw
    loaded = raw.pop("_loaded_values", {})
    for field in set(loaded) | (set(item) - {"raw_payload"}):
        if field not in item:
            raw["_loader_values"].pop(field, None)
        elif field not in loaded or item[field] != loaded[field]:
            raw["_loader_values"][field] = [deepcopy(item[field])]
    return raw


class RecordLoader(ItemLoader):
    """Use native add_css/add_xpath/add_value for every source format."""

    default_item_class = RecordItem
    default_input_processor = str_clean
    default_output_processor = TakeFirst()

    # Source IDs are opaque: do not trim whitespace, strip markup or change case.
    source_key_in = Identity()
    latitude_in = float_clean
    longitude_in = float_clean
    url_in = MapCompose(absolute_url)
    full_address_out = Join(", ")
    street_address_out = Join(" ")
    features_in = Identity()
    features_out = clean_features

    def __init__(self, spider, *args, **kwargs):
        initial = source_payload(dict(kwargs.get("item") or {}))
        self._raw_values = initial.get("_loader_values", {
            field: [value] for field, value in initial.items()
        })
        self._source_base_url = initial.get("_base_url")
        super().__init__(*args, **kwargs)
        self.add_value(RecordFields.SOURCE, getattr(spider, "source_id", spider.name))
        self.add_value(RecordFields.TIMESTAMP, spider.timestamp)

    def add_feature(self, name, value, unit=None):
        self.add_value("features", {name: {"value": value, "unit": unit}})

    def _add_value(self, field_name, value):
        values = list(arg_to_iter(value))
        self._raw_values.setdefault(field_name, []).extend(deepcopy(values))
        super()._add_value(field_name, values)

    def _replace_value(self, field_name, value):
        self._raw_values.pop(field_name, None)
        super()._replace_value(field_name, value)

    def _process_input_value(self, field_name, value):
        # Invalid business values must reach durable source staging too.
        try:
            return super()._process_input_value(field_name, value)
        except (ValueError, TypeError):
            return value

    def get_output_value(self, field_name):
        try:
            return super().get_output_value(field_name)
        except (ValueError, TypeError):
            return self._raw_values.get(field_name)

    def load_item(self):
        item = super().load_item()
        response = self.context.get("response")
        item["raw_payload"] = {
            "_loader_values": deepcopy(self._raw_values),
            "_base_url": self._source_base_url or (response.url if response is not None else None),
            "_loaded_values": deepcopy({field: value for field, value in item.items() if field != "raw_payload"}),
        }
        return item
