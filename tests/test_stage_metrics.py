from tariff_agent.stage_metrics import agent_funnel, retrieval_funnel


def test_missing_stages_remain_in_overall_retention_denominator():
    values = [
        {
            "gold_code": "9901010101",
            "status": "awaiting_review",
            "retrieved": ["9901"],
            "shortlist": ["9901"],
            "ranked": ["9901"],
            "resolved": ["9901010101"],
            "attributes_extracted": True,
        },
        {"gold_code": "9901010101", "status": "failed"},
    ]
    result = agent_funnel(values)
    assert result["stages"]["ranked"]["reference_heading_retention_all_cases"]["value"] == 0.5
    assert result["stages"]["ranked"]["reference_heading_retention_reached_cases"]["value"] == 1
    assert result["semantic_attribute_accuracy"] is None


def test_retrieval_metrics_use_recorded_order_and_do_not_credit_unrecorded_channels():
    records = [
        {
            "gold_code": "990101",
            "status": "completed",
            "retrieval_stages": {"catalog_recall": ["9901"], "fused_recall": ["9901"], "reranked": ["9901"]},
        }
    ]
    result = retrieval_funnel(records)
    assert result["stages"]["catalog_recall"]["recall@40"]["value"] == 1
    assert result["stages"]["history_heading_recall"]["recorded_cases"] == 0
    assert result["stages"]["history_heading_recall"]["recall@40"]["value"] == 0


def test_pilot_visibility_requires_explicit_active_parent_and_unique_sample_ids():
    from tariff_agent.evaluation_api import active_experiment

    manifest = {"files": {"dev.jsonl": "parent-hash"}, "counts": {"dev": 100}}
    pilot = {
        "pilot": True,
        "split": "dev",
        "parent_dataset_sha256": "parent-hash",
        "dataset_sha256": "subset-hash",
        "n_completed": 2,
        "selected_ids": ["a", "b"],
    }
    assert active_experiment(pilot, manifest)
    assert not active_experiment(pilot | {"pilot": False}, manifest)
    assert not active_experiment(pilot | {"parent_dataset_sha256": "old-hash"}, manifest)
    assert not active_experiment(pilot | {"selected_ids": ["a", "a"]}, manifest)
