package core

import "testing"

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
