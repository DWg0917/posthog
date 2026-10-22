package core

import (
	"os"
	"strings"
	"testing"
)

func TestCustomDeploymentCredentials(t *testing.T) {
	template, err := os.ReadFile("../../../.env.services")
	if err != nil {
		t.Fatal(err)
	}
	t.Chdir(t.TempDir())
	if err := os.WriteFile(".env.services", template, 0600); err != nil {
		t.Fatal(err)
	}
	config := CustomDeployConfig{
		FeatureFlagsImage: "example/posthog:feature-flags-compatible",
		PostgresUser:      "posthog", PostgresPassword: strings.Repeat("a", 64),
		ClickHouseAppPassword:    strings.Repeat("b", 64),
		ClickHouseAPISecret:      strings.Repeat("c", 64),
		ClickHouseReaderPassword: strings.Repeat("d", 64),
		RedisPassword:            strings.Repeat("e", 64),
		ObjectStoragePassword:    strings.Repeat("f", 64),
	}
	if err := writeCustomDeploymentFiles(config, "test-hash"); err != nil {
		t.Fatal(err)
	}
	values := map[string]string{}
	for _, name := range []string{".env", ".env.services.runtime"} {
		data, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(name)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0600 {
			t.Fatalf("%s must be private", name)
		}
		for _, line := range strings.Split(string(data), "\n") {
			key, value, ok := strings.Cut(line, "=")
			if ok {
				values[key] = strings.Trim(value, "'")
			}
		}
	}
	password := values["CLICKHOUSE_DEFAULT_PASSWORD"]
	if len(password) != 64 || password == config.ClickHouseAppPassword {
		t.Fatal("default must have a separate generated strong password")
	}
	if values["CLICKHOUSE_BILLING_USER"] != "app" || values["CLICKHOUSE_BILLING_PASSWORD"] != config.ClickHouseAppPassword {
		t.Fatal("billing must use an account provisioned by this deployment")
	}
	for key, want := range map[string]string{
		"POSTHOG_FEATURE_FLAGS_IMAGE":     config.FeatureFlagsImage,
		"CLICKHOUSE_APP_PASSWORD":         config.ClickHouseAppPassword,
		"CLICKHOUSE_API_PASSWORD":         config.ClickHouseAPISecret,
		"CLICKHOUSE_DICT_READER_PASSWORD": config.ClickHouseReaderPassword,
	} {
		if values[key] != want {
			t.Fatalf("%s was not propagated", key)
		}
	}
}

func TestValidateDatabaseUsername(t *testing.T) {
	tests := []struct {
		username string
		valid    bool
	}{
		{username: "posthog", valid: true},
		{username: "db_user_2", valid: true},
		{username: "", valid: false},
		{username: "2user", valid: false},
		{username: "user-name", valid: false},
	}

	for _, test := range tests {
		t.Run(test.username, func(t *testing.T) {
			err := ValidateDatabaseUsername(test.username)
			if (err == nil) != test.valid {
				t.Fatalf("ValidateDatabaseUsername(%q) error = %v, want valid=%t", test.username, err, test.valid)
			}
		})
	}
}

func TestAIEnvironmentForWizard(t *testing.T) {
	tests := []struct {
		provider, key, base, models, defaultBase string
	}{
		{"glm", "GLM_API_KEY", "GLM_BASE_URL", "GLM_SUPPORTED_MODELS", "https://open.bigmodel.cn/api/coding/paas/v4"},
		{"custom", "CUSTOM_LLM_API_KEY", "CUSTOM_LLM_BASE_URL", "CUSTOM_LLM_MODELS", ""},
	}

	for _, test := range tests {
		t.Run(test.provider, func(t *testing.T) {
			key, base, models, defaultBase, err := AIEnvironmentForWizard(test.provider)
			if err != nil {
				t.Fatal(err)
			}
			if key != test.key || base != test.base || models != test.models || defaultBase != test.defaultBase {
				t.Fatalf("AIEnvironmentForWizard(%q) = (%q, %q, %q, %q)", test.provider, key, base, models, defaultBase)
			}
		})
	}
}

func TestReplaceEnvValues(t *testing.T) {
	content := "DATABASE_URL=old\nPGUSER=old\nKEEP=value\n"
	result, err := replaceEnvValues(content, map[string]string{"DATABASE_URL": "postgres://user:secret@db", "PGUSER": "posthog"})
	if err != nil {
		t.Fatal(err)
	}
	want := "DATABASE_URL=postgres://user:secret@db\nPGUSER=posthog\nKEEP=value\n"
	if result != want {
		t.Fatalf("replaceEnvValues() = %q, want %q", result, want)
	}
}
