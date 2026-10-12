package ai.solar.control.decision;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import ai.solar.control.Fixtures;
import ai.solar.control.inference.InferenceException;
import ai.solar.control.inference.InferenceException.Reason;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.node.ObjectNode;

class DecisionFactsTests {

	@Test
	void copiesTheDecisionExactlyAsTheInferenceApiWroteIt() {
		DecisionFacts facts = DecisionFacts.from(Fixtures.rotatePayload());

		assertThat(facts.action()).isEqualTo("ROTATE");
		assertThat(facts.targetAngleDeg()).isEqualTo(30.0);
		assertThat(facts.intervalStart()).isEqualTo(Instant.parse("2025-06-05T19:00:00Z"));
		assertThat(facts.controlTargetId()).isEqualTo("row-001");
		assertThat(facts.selectedModel()).isEqualTo("lstm");
		assertThat(facts.dataStatus()).isEqualTo("DEGRADED");
		assertThat(facts.safetyPassed()).isTrue();
		assertThat(facts.energyGainKwh()).isEqualTo(0.8081979751586914);
		assertThat(facts.movementCostKwhEquivalent()).isEqualTo(0.09);
		assertThat(facts.netBenefitKwhEquivalent()).isEqualTo(0.7181979751586914);
		assertThat(facts.reason()).startsWith("Rotate to 30 deg");
	}

	@Test
	void figuresAreNullNotZeroWhenThereIsNoOptimizationResult() {
		ObjectNode payload = Fixtures.holdPayload(35);
		payload.putNull("optimization");
		payload.putNull("selected_model");

		DecisionFacts facts = DecisionFacts.from(payload);

		assertThat(facts.energyGainKwh()).isNull();
		assertThat(facts.movementCostKwhEquivalent()).isNull();
		assertThat(facts.netBenefitKwhEquivalent()).isNull();
		assertThat(facts.selectedModel()).isNull();
	}

	@Test
	void refusesAPayloadWithNoDecision() {
		ObjectNode payload = Fixtures.rotatePayload();
		payload.remove("decision");

		assertThatThrownBy(() -> DecisionFacts.from(payload)).isInstanceOfSatisfying(InferenceException.class,
				ex -> assertThat(ex.reason()).isEqualTo(Reason.MALFORMED));
	}

	@Test
	void refusesAnActionOutsideTheThreeAllowed() {
		ObjectNode payload = Fixtures.rotatePayload();
		((ObjectNode) payload.get("decision")).put("action", "SPIN");

		assertThatThrownBy(() -> DecisionFacts.from(payload)).isInstanceOf(InferenceException.class).hasMessageContaining("decision.action");
	}

	@Test
	void refusesATargetAngleThatIsNotANumber() {
		ObjectNode payload = Fixtures.rotatePayload();
		((ObjectNode) payload.get("decision")).put("target_angle_deg", "steep");

		assertThatThrownBy(() -> DecisionFacts.from(payload)).isInstanceOf(InferenceException.class).hasMessageContaining("target_angle_deg");
	}

	@Test
	void refusesAnIntervalStartThatIsNotATimestamp() {
		ObjectNode payload = Fixtures.rotatePayload();
		((ObjectNode) payload.get("metadata")).put("interval_start", "yesterday");

		assertThatThrownBy(() -> DecisionFacts.from(payload)).isInstanceOf(InferenceException.class).hasMessageContaining("interval_start");
	}

	@Test
	void cutsAnOverlongReasonToFitTheColumn() {
		ObjectNode payload = Fixtures.rotatePayload();
		((ObjectNode) payload.get("decision")).put("reason", "x".repeat(5000));

		assertThat(DecisionFacts.from(payload).reason()).hasSize(2000);
	}
}
