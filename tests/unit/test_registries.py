"""The extension points agree with each other (docs/adding-a-feature.md)."""

import pytest

from fuse2sc import model
from fuse2sc.replay import strategies
from fuse2sc.translate.features import plan_feature


def test_every_payload_model_has_a_translator_handler():
	handled = set(plan_feature.registry)
	missing = {kind for kind, cls in model.PAYLOADS.items() if cls not in handled}
	assert not missing, f"no handler in translate/features.py for {sorted(missing)}"


def test_a_kind_can_only_be_registered_once():
	with pytest.raises(ValueError, match="already has a payload model"):
		model.payload("ExtrudeFeature")(type("Duplicate", (model.Feature,), {}))


def test_every_payload_model_has_a_json_schema():
	schema = model.json_schema()
	assert set(schema["x-feature-payloads"]) == set(model.PAYLOADS)


def test_strategies_are_well_formed():
	names = [s.name for s in strategies.STRATEGIES]
	assert len(names) == len(set(names))
	assert all(s.why and callable(s.applies) and callable(s.run) for s in strategies.STRATEGIES)
