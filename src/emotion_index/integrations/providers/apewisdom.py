"""ApeWisdom public Reddit stock mention aggregates."""

from emotion_index.integrations.providers.http import Feed, json_list


def feeds() -> tuple[Feed, ...]:
    return (Feed("apewisdom", "all-stocks", "https://apewisdom.io/api/v1.0/filter/all-stocks",
                 json_list("results")),)
