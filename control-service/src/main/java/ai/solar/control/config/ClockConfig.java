package ai.solar.control.config;

import java.time.Clock;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class ClockConfig {

	/** A bean, so tests can fix the time. */
	@Bean
	Clock clock() {
		return Clock.systemUTC();
	}
}
