package ai.solar.control;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Control service for the Solar Farm AI system.
 *
 * <p>It never decides anything itself. Every decision comes from the Python inference API; this
 * service asks for decisions (on request or on a schedule), stores them as received, and tracks the
 * simulated position of the controlled row so each request starts from where the last one left it.
 */
@SpringBootApplication
@ConfigurationPropertiesScan
@EnableScheduling
public class ControlServiceApplication {

	public static void main(String[] args) {
		SpringApplication.run(ControlServiceApplication.class, args);
	}

}
