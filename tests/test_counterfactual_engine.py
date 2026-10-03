"""Tests for Module AM: Programmatic SCM & Counterfactual Engine."""
import pytest
from counterfactual_engine import (
    CounterfactualResult,
    StructuralCausalModel,
    add_variable,
    evaluate_counterfactual,
    get_counterfactual_engine,
)


class TestCounterfactualEngine:
    def _build_fire_sprinkler_scm(self) -> StructuralCausalModel:
        """Classic Pearl SCM: Season -> Rain, Sprinkler -> Wet Grass."""
        scm = StructuralCausalModel()

        # Season (0=Dry, 1=Wet) + exogenous u_season
        scm.add_variable(
            "Season",
            parents=[],
            equation_fn=lambda pa, u: int(u),
            exogenous_sampler=lambda: 1,
            inverter_fn=lambda x, pa: x,
        )

        # Sprinkler (turned on if dry season and u_sprinkler=1)
        scm.add_variable(
            "Sprinkler",
            parents=["Season"],
            equation_fn=lambda pa, u: int(pa["Season"] == 0 or u == 1),
            exogenous_sampler=lambda: 0,
            inverter_fn=lambda x, pa: 1 if x == 1 and pa["Season"] == 1 else 0,
        )

        # Rain (rains if wet season or u_rain=1)
        scm.add_variable(
            "Rain",
            parents=["Season"],
            equation_fn=lambda pa, u: int(pa["Season"] == 1 or u == 1),
            exogenous_sampler=lambda: 1,
            inverter_fn=lambda x, pa: 1 if x == 1 and pa["Season"] == 0 else 0,
        )

        # WetGrass (wet if Sprinkler or Rain)
        scm.add_variable(
            "WetGrass",
            parents=["Sprinkler", "Rain"],
            equation_fn=lambda pa, u: int(pa["Sprinkler"] == 1 or pa["Rain"] == 1 or u == 1),
            exogenous_sampler=lambda: 0,
            inverter_fn=lambda x, pa: 1 if x == 1 and pa["Sprinkler"] == 0 and pa["Rain"] == 0 else 0,
        )

        return scm

    def test_forward_factual_sampling(self):
        scm = self._build_fire_sprinkler_scm()
        values, u_used = scm.sample_factual()
        assert values["Season"] == 1
        assert values["Rain"] == 1
        assert values["WetGrass"] == 1

    def test_counterfactual_intervention_abduction(self):
        """Suppose it rained (Season=1, Rain=1), Sprinkler was OFF (0), WetGrass was ON (1).

        Counterfactual query: If it had NOT rained do(Rain=0), would the grass still be wet?
        Prediction: Sprinkler was OFF (since Season=1), so without Rain, WetGrass should be 0.
        """
        scm = self._build_fire_sprinkler_scm()
        factual = {"Season": 1, "Sprinkler": 0, "Rain": 1, "WetGrass": 1}

        cf_res = scm.evaluate_counterfactual(
            factual_observation=factual,
            intervention_do={"Rain": 0},
            target_success_key="WetGrass",
        )

        assert cf_res.counterfactual_outcome["Rain"] == 0
        assert cf_res.counterfactual_outcome["WetGrass"] == 0
        assert cf_res.counterfactual_outcome["Sprinkler"] == 0

    def test_post_failure_regret_analysis(self):
        """Test failure scenario in task execution."""
        scm = StructuralCausalModel()

        # Model: Strategy -> Verification -> TaskSuccess
        scm.add_variable(
            "Strategy",
            parents=[],
            equation_fn=lambda pa, u: str(u),
            exogenous_sampler=lambda: "fast_greedy",
            inverter_fn=lambda x, pa: x,
        )

        scm.add_variable(
            "Verification",
            parents=["Strategy"],
            equation_fn=lambda pa, u: pa["Strategy"] == "careful_deliberation",
            exogenous_sampler=lambda: False,
        )

        scm.add_variable(
            "TaskSuccess",
            parents=["Verification"],
            equation_fn=lambda pa, u: bool(pa["Verification"] or u),
            exogenous_sampler=lambda: False,
            inverter_fn=lambda x, pa: bool(x and not pa["Verification"]),
        )

        # Factual failure with fast_greedy
        factual = {"Strategy": "fast_greedy", "Verification": False, "TaskSuccess": False}

        # Counterfactual: do(Strategy = 'careful_deliberation')
        cf_res = scm.evaluate_counterfactual(
            factual_observation=factual,
            intervention_do={"Strategy": "careful_deliberation"},
            target_success_key="TaskSuccess",
        )

        assert cf_res.counterfactual_outcome["TaskSuccess"] is True
        assert cf_res.prevented_failure is True
        assert cf_res.regret_score == 1.0
