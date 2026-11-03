package tui

import "testing"

func TestKafkaUIWizardFields(t *testing.T) {
	m, err := newCustomModel()
	if err != nil {
		t.Fatal(err)
	}
	fields := map[string]customField{}
	for _, f := range m.fields {
		fields[f.key] = f
	}
	for _, key := range []string{"kafka_domain", "kafka_username", "kafka_password"} {
		if _, ok := fields[key]; !ok {
			t.Fatalf("missing Kafka UI field %s", key)
		}
	}
	if !fields["kafka_domain"].optional || !fields["kafka_password"].secret {
		t.Fatal("Kafka UI must be optional and its password masked")
	}
}

func TestKafkaUIRequiresCredentialsWhenEnabled(t *testing.T) {
	fields := []customField{
		{key: "kafka_domain", value: "kafka.example.com"},
		{key: "app_image", value: "example/app:test"},
		{key: "node_image", value: "example/node:test"},
		{key: "feature_flags_image", value: "example/flags:test"},
	}
	if _, err := customConfigFromFields(fields); err == nil {
		t.Fatal("Kafka UI without credentials must be rejected")
	}
}
