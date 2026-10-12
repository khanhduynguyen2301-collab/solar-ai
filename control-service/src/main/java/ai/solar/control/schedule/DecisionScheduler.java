package ai.solar.control.schedule;

import ai.solar.control.config.SolarProperties;
import ai.solar.control.decision.DecisionRecord;
import ai.solar.control.decision.DecisionRecord.TriggerSource;
import ai.solar.control.decision.DecisionService;
import ai.solar.control.decision.DecisionService.DecisionCommand;
import ai.solar.control.inference.InferenceException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Requests a decision on a schedule and applies it to the simulated row.
 *
 * <p>Only exists when {@code solar.schedule.enabled=true}. A failed run is logged and skipped;
 * the next run starts from the unchanged row position.
 */
@Component
@ConditionalOnProperty(prefix = "solar.schedule", name = "enabled", havingValue = "true")
public class DecisionScheduler {

	private static final Logger LOGGER = LoggerFactory.getLogger(DecisionScheduler.class);

	private final DecisionService service;
	private final SolarProperties properties;

	public DecisionScheduler(DecisionService service, SolarProperties properties) {
		this.service = service;
		this.properties = properties;
	}

	@Scheduled(cron = "${solar.schedule.cron}", zone = "UTC")
	public void run() {
		try {
			DecisionRecord record = service.decide(new DecisionCommand(properties.schedule().mode(), null, null, false), TriggerSource.SCHEDULED);
			LOGGER.info("Scheduled decision {}: {} to {} deg", record.getId(), record.getAction(), record.getTargetAngleDeg());
		}
		catch (InferenceException ex) {
			LOGGER.warn("Scheduled decision skipped ({}): {}", ex.reason(), ex.getMessage());
		}
		catch (RuntimeException ex) {
			LOGGER.error("Scheduled decision failed", ex);
		}
	}
}
