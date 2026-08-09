# This machine's DNS server is a link-local IPv6 address (fe80::1), which the
# c-ares resolver (aiohttp's AsyncResolver, used automatically because ccxt
# hard-depends on aiodns) cannot query — every request fails with
# "Could not contact DNS servers". Pin aiohttp back to its stdlib-backed
# ThreadedResolver, which handles it fine, before any connector is built.
import aiohttp.connector
from aiohttp.resolver import ThreadedResolver

aiohttp.connector.DefaultResolver = ThreadedResolver
