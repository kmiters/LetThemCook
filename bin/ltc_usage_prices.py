"""API Standard list prices, USD per million tokens. Offline snapshot."""
from typing import NamedTuple

VERSION = '2026-11-19'
OPENAI = 'https://developers.openai.com/api/docs/pricing'
CLAUDE = 'https://platform.claude.com/docs/en/about-claude/pricing'
# Each GPT's context rule is also documented at
# https://developers.openai.com/api/docs/models/<exact-model-id>.


class Rates(NamedTuple):
    fresh: float
    read: float
    write_short: float
    write_long: float
    output: float


# OpenAI writes: 30m / 12h. Anthropic writes: 5m / 1h.
STANDARD = {
    'gpt-6.2-astra': Rates(8, .40, 10, 20, 40),
    'gpt-6.2-sol': Rates(2, .10, 2.50, 5, 10),
    'gpt-6-astra': Rates(10, 1, 12.50, 25, 50),
    'gpt-6.1-sol': Rates(2, .10, 2.50, 5, 10),
    'gpt-6.1-terra': Rates(1, .05, 1.25, 2.50, 5),
    'gpt-6-luna': Rates(.10, .01, .125, .25, .50),
    'claude-opus-5-5': Rates(4, .20, 5, 8, 20),
    'claude-sonnet-5-5': Rates(2, .10, 2.50, 4, 10),
    'claude-haiku-5-5': Rates(.10, .01, .125, .20, .50),
}
HAIKU_LONG = Rates(.50, .05, .625, 1, 2.50)
COMPONENTS = ('fresh', 'read', 'write', 'output')


def estimate(model, counts, write_split=(0, 0), single=True):
    """Return component costs in integer nano-USD, or None for unpriced usage."""
    rates = STANDARD.get(model)
    if rates is None or not single:
        return None
    fresh, read, write, output, _ = counts
    prompt = fresh + read + write
    if model == 'claude-haiku-5-5' and prompt > 100_000:
        rates = HAIKU_LONG
    long_gpt = model.startswith('gpt-') and prompt > 272_000
    input_factor = 2 if long_gpt else 1
    output_factor = 1.5 if long_gpt else 1
    _, long_write = write_split
    # Unknown retention is estimated at the short rate.
    return (fresh * round(rates.fresh * input_factor * 1000),
            read * round(rates.read * input_factor * 1000),
            (write-long_write) * round(rates.write_short * input_factor * 1000)
            + long_write * round(rates.write_long * input_factor * 1000),
            output * round(rates.output * output_factor * 1000))
