# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

from enum import StrEnum

import scrapy


class RecordFields(StrEnum):
    SOURCE_KEY = "source_key"
    TIMESTAMP = "timestamp"
    SOURCE = "source"
    URL = "url"
    NAME = "name"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    FULL_ADDRESS = "full_address"
    COUNTRY = "country"
    STATE = "state"
    CITY = "city"
    POSTAL_CODE = "postal_code"
    STREET_NAME = "street_name"
    STREET_NUMBER = "street_number"
    STREET_ADDRESS = "street_address"
    FEATURES = "features"


class RecordItem(scrapy.Item):
    raw_payload = scrapy.Field()
    for field in RecordFields:
        vars()[field.value] = scrapy.Field()
