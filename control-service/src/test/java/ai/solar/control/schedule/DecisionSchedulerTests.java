package ai.solar.control.schedule;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import ai.solar.control.config.SolarProperties;
import ai.solar.control.decision.DecisionRecord;
import ai.solar.control.decision.DecisionRecord.TriggerSource;
import ai.solar.control.decision.DecisionService;
import ai.solar.control.decision.DecisionService.DecisionCommand;
import ai.solar.control.inference.InferenceException;
import ai.solar.control.inference.InferenceException.Reason;
import java.net.URI;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

class DecisionSchedulerTests {

	private final DecisionService service = mock(DecisionService.class);
	private final SolarProperties properties = new SolarProperties(
			new SolarProperties.Inference(URI.create("http://inference.test"), Duration.ofSeconds(1), Duration.ofSeconds(1), null),
			new SolarProperties.Row("row-001", 35), new SolarProperties.Schedule(true, "0 5 * * * *", "live"));
	private final DecisionScheduler scheduler = new DecisionScheduler(service, properties);

	@Test
	void asksForAnAppliedDecisionInTheConfiguredMode() {
		when(service.decide(any(), any())).thenReturn(mock(DecisionRecord.class));

		scheduler.run();

		ArgumentCaptor<DecisionCommand> command = ArgumentCaptor.forClass(DecisionCommand.class);
		verify(service).decide(command.capture(), eq(TriggerSource.SCHEDULED));
		// No angle: the service uses, and then updates, the simulated row position.
		assertThat(command.getValue()).isEqualTo(new DecisionCommand("live", null, null, false));
	}

	@Test
	void anInferenceOutageIsSkippedSoTheNextRunStillHappens() {
		when(service.decide(any(), any())).thenThrow(new InferenceException(Reason.UNAVAILABLE, "down"));

		assertThatCode(scheduler::run).doesNotThrowAnyException();
	}

	@Test
	void anUnexpectedFailureDoesNotEscapeEither() {
		when(service.decide(any(), any())).thenThrow(new IllegalStateException("database offline"));

		assertThatCode(scheduler::run).doesNotThrowAnyException();
	}
}
