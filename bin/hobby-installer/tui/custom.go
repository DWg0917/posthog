package tui

import (
	"fmt"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/posthog/posthog/bin/hobby-installer/core"
	"github.com/posthog/posthog/bin/hobby-installer/ui"
)

type customField struct {
	key, label, help, value string
	secret, optional        bool
}

type customTemporalWorkerGroup struct {
	profile, label, description string
}

var customTemporalWorkerGroups = []customTemporalWorkerGroup{
	{profile: "worker-general-purpose", label: "General purpose", description: "Shared queue used by general workflows"},
	{profile: "worker-data-warehouse", label: "Data warehouse", description: "Imports, metadata, modeling, and DuckLake"},
	{profile: "worker-exports", label: "Exports", description: "Batch exports, video exports, and digests"},
	{profile: "worker-experiments-billing-tasks", label: "Experiments, billing, and tasks", description: "Recalculations, billing, and task workflows"},
	{profile: "worker-session-replay", label: "Session replay", description: "Session replay processing"},
	{profile: "worker-error-tracking-logs", label: "Error tracking and logs", description: "Error tracking, screenshots, and logs"},
	{profile: "worker-ai-analytics", label: "AI analytics", description: "LLM analytics and evaluations"},
	{profile: "worker-system", label: "System", description: "Health checks, StampHog, and test workflows"},
	{profile: "worker-replay-vision", label: "Replay vision", description: "Dedicated Replay Vision queue; enable only when needed"},
}

type customModel struct {
	fields                []customField
	index                 int
	workerGroupIndex      int
	selectedWorkerGroups  map[string]bool
	selectingWorkerGroups bool
	input                 textinput.Model
	installing, complete  bool
	err                   error
	spinner               spinner.Model
}

type customDoneMsg struct{ err error }
type customLogTickMsg struct{}

func newCustomModel() (customModel, error) {
	defaults := make([]string, 6)
	for i := range defaults {
		password, err := core.GenerateDeploymentPassword()
		if err != nil {
			return customModel{}, err
		}
		defaults[i] = password
	}
	fields := []customField{
		{key: "domain", label: "PostHog domain", help: "DNS A record must point to this server; enter host only", value: "dwg.asia"},
		{key: "temporal_domain", label: "Temporal UI domain", help: "Create a DNS A record for this host too", value: "temporal.dwg.asia"},
		{key: "dockerhub_user", label: "Docker Hub username", help: "Used for docker login on this server", value: "diweigao"},
		{key: "dockerhub_token", label: "Docker Hub access token", help: "Prefer a read-only token; stored only in Docker's credential config", secret: true},
		{key: "postgres_user", label: "PostgreSQL username", help: "External PostgreSQL login; letters, digits, underscore", value: "posthog"},
		{key: "postgres_password", label: "PostgreSQL password", help: "Generated strong default; press Enter to keep it", value: defaults[0], secret: true},
		{key: "clickhouse_app_password", label: "ClickHouse app password", help: "Login username is app; used by PostHog and external clients", value: defaults[1], secret: true},
		{key: "clickhouse_api_password", label: "ClickHouse API password", help: "Generated credential for PostHog's API user", value: defaults[2], secret: true},
		{key: "clickhouse_reader_password", label: "ClickHouse read-only password", help: "Generated credential for the dict_reader service user", value: defaults[3], secret: true},
		{key: "redis_password", label: "Redis password", help: "Generated credential; used by all PostHog services", value: defaults[4], secret: true},
		{key: "object_storage_user", label: "Object storage access key", help: "Generated S3/MinIO username", value: "posthog_" + defaults[5][:16]},
		{key: "object_storage_password", label: "Object storage secret", help: "Generated S3/MinIO password", value: defaults[5], secret: true},
		{key: "caddy_username", label: "Temporal UI login username", help: "HTTP Basic Auth username", value: "admin"},
		{key: "caddy_password", label: "Temporal UI login password", help: "Caddy stores a bcrypt hash, never plaintext", secret: true},
		{key: "ai_provider", label: "AI provider", help: "none, glm, mimo, qwen, openai, anthropic, or custom", value: "glm", optional: true},
		{key: "ai_model", label: "AI model", help: "For example glm-5.3; leave empty when provider is none", value: "glm-5.3", optional: true},
		{key: "ai_base_url", label: "AI Base URL", help: "Optional; leave empty to use the provider default", optional: true},
		{key: "ai_api_key", label: "AI API key", help: "Stored in the private .env file", secret: true, optional: true},
		{key: "app_image", label: "PostHog app image", help: "Docker Hub image including tag", value: "diweigao/my-posthog-single:app-ec70230fe1"},
		{key: "node_image", label: "PostHog node image", help: "Docker Hub image including tag", value: "diweigao/my-posthog-single:node-ec70230fe1"},
		{key: "feature_flags_image", label: "Feature Flags image", help: "Docker Hub image built for the app database schema, including tag", value: "diweigao/my-posthog-single:feature-flags-70a28660e31"},
	}
	input := textinput.New()
	input.CharLimit = 512
	input.Width = 68
	m := customModel{fields: fields, input: input, selectedWorkerGroups: map[string]bool{"worker-general-purpose": true}}
	m.focusField()
	return m, nil
}

func (m *customModel) focusField() {
	f := m.fields[m.index]
	m.input = textinput.New()
	m.input.CharLimit = 512
	m.input.Width = 68
	m.input.SetValue(f.value)
	if f.secret {
		m.input.EchoMode = textinput.EchoPassword
	}
	m.input.Focus()
}

func (m customModel) Init() tea.Cmd { return textinput.Blink }

func (m customModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case customLogTickMsg:
		return m, tea.Tick(500_000_000, func(time.Time) tea.Msg { return customLogTickMsg{} })
	case customDoneMsg:
		m.installing = false
		m.complete = true
		m.err = msg.err
		return m, nil
	case spinner.TickMsg:
		var cmd tea.Cmd
		m.spinner, cmd = m.spinner.Update(msg)
		return m, cmd
	case tea.KeyMsg:
		if m.installing {
			if msg.String() == "ctrl+c" {
				return m, tea.Quit
			}
			return m, nil
		}
		if m.complete {
			if msg.String() == "enter" || msg.String() == "q" {
				return m, tea.Quit
			}
			return m, nil
		}
		if m.selectingWorkerGroups {
			switch msg.String() {
			case "ctrl+c":
				return m, tea.Quit
			case "esc":
				m.selectingWorkerGroups = false
				m.focusField()
				return m, textinput.Blink
			case "up", "k":
				if m.workerGroupIndex > 0 {
					m.workerGroupIndex--
				}
			case "down", "j":
				if m.workerGroupIndex < len(customTemporalWorkerGroups)-1 {
					m.workerGroupIndex++
				}
			case " ":
				profile := customTemporalWorkerGroups[m.workerGroupIndex].profile
				m.selectedWorkerGroups[profile] = !m.selectedWorkerGroups[profile]
			case "enter":
				profiles := make([]string, 0, len(m.selectedWorkerGroups))
				for _, group := range customTemporalWorkerGroups {
					if m.selectedWorkerGroups[group.profile] {
						profiles = append(profiles, group.profile)
					}
				}
				config, err := customConfigFromFields(m.fields)
				if err != nil {
					m.err = err
					return m, nil
				}
				config.TemporalWorkerProfiles = profiles
				m.err = nil
				m.selectingWorkerGroups = false
				m.installing = true
				m.spinner = spinner.New()
				m.spinner.Spinner = spinner.Dot
				m.spinner.Style = ui.SpinnerStyle
				return m, tea.Batch(m.spinner.Tick, m.install(config), func() tea.Msg { return customLogTickMsg{} })
			}
			return m, nil
		}
		switch msg.String() {
		case "ctrl+c":
			return m, tea.Quit
		case "esc":
			if m.index > 0 {
				m.index--
				m.focusField()
			}
			return m, nil
		case "enter":
			m.fields[m.index].value = strings.TrimSpace(m.input.Value())
			if err := validateCustomField(m.fields[m.index]); err != nil {
				m.err = err
				return m, nil
			}
			m.err = nil
			if m.index < len(m.fields)-1 {
				m.index++
				m.focusField()
				return m, textinput.Blink
			}
			m.selectingWorkerGroups = true
			m.workerGroupIndex = 0
			return m, nil
		}
	}
	if !m.installing && !m.complete {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m customModel) install(config core.CustomDeployConfig) tea.Cmd {
	return func() tea.Msg { return customDoneMsg{err: core.RunCustomDeployment(config)} }
}

func (m customModel) View() string {
	if m.complete {
		if m.err != nil {
			return ui.TitleStyle.Render("PostHog deployment failed") + "\n\n" + ui.ErrorStyle.Render(m.err.Error()) + "\n\n" + ui.HelpStyle.Render("See hobby-installer.log; press enter to exit")
		}
		return ui.TitleStyle.Render("PostHog deployment complete") + "\n\n" + ui.SuccessStyle.Render("PostHog is ready at https://"+m.fields[0].value) + "\n" + ui.MutedStyle.Render("Temporal UI: https://"+m.fields[1].value) + "\n\n" + ui.HelpStyle.Render("Press enter to exit")
	}
	if m.installing {
		logs := core.GetLogger().GetLines(8)
		return lipgloss.JoinVertical(lipgloss.Left, ui.TitleStyle.Render("Deploying custom PostHog"), "", m.spinner.View()+" Running deployment steps", "", ui.MutedStyle.Render(strings.Join(logs, "\n")), "", ui.HelpStyle.Render("Keep this terminal open; secrets are hidden"))
	}
	if m.selectingWorkerGroups {
		lines := []string{
			ui.TitleStyle.Render("Choose Temporal worker groups"),
			ui.SubtitleStyle.Render("Selected groups start during deployment and can be changed later."),
			"",
		}
		for index, group := range customTemporalWorkerGroups {
			marker := "[ ]"
			if m.selectedWorkerGroups[group.profile] {
				marker = "[x]"
			}
			line := fmt.Sprintf("%s %s — %s", marker, group.label, group.description)
			if index == m.workerGroupIndex {
				line = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("205")).Render("> " + line)
			} else {
				line = "  " + line
			}
			lines = append(lines, line)
		}
		lines = append(lines, "", ui.MutedStyle.Render("↑/↓: move / Space: toggle / Enter: deploy / Esc: previous step"))
		if m.err != nil {
			lines = append(lines, ui.ErrorStyle.Render(m.err.Error()))
		}
		return lipgloss.NewStyle().Padding(2, 4).Render(lipgloss.JoinVertical(lipgloss.Left, lines...))
	}
	f := m.fields[m.index]
	secretWarning := ""
	if f.secret {
		secretWarning = ui.WarningStyle.Render("Input is hidden")
	}
	content := lipgloss.JoinVertical(lipgloss.Left,
		ui.TitleStyle.Render("Custom PostHog single-server deployment"),
		ui.SubtitleStyle.Render(fmt.Sprintf("Step %d of %d", m.index+1, len(m.fields))),
		"", lipgloss.NewStyle().Bold(true).Render(f.label), ui.MutedStyle.Render(f.help), "",
		m.input.View(), secretWarning,
		"", ui.MutedStyle.Render("Enter: confirm / Esc: previous step / Ctrl+C: cancel"),
		ui.MutedStyle.Render("Generated passwords are shown masked; Enter keeps the generated value."),
	)
	if m.err != nil {
		content += "\n" + ui.ErrorStyle.Render(m.err.Error())
	}
	return lipgloss.NewStyle().Padding(2, 4).Render(content)
}

func validateCustomField(field customField) error {
	value := strings.TrimSpace(field.value)
	if value == "" && !field.optional {
		return fmt.Errorf("%s is required", field.label)
	}
	switch field.key {
	case "domain", "temporal_domain":
		if strings.Contains(value, "://") || strings.ContainsAny(value, "/ \t\n") || net.ParseIP(value) != nil || !strings.Contains(value, ".") {
			return fmt.Errorf("enter a hostname, not a URL or IP address")
		}
	case "postgres_user":
		return core.ValidateDatabaseUsername(value)
	case "object_storage_user":
		return core.ValidateDatabaseUsername(value)
	case "postgres_password", "clickhouse_app_password", "clickhouse_api_password", "clickhouse_reader_password", "redis_password", "object_storage_password":
		if len(value) < 20 || !databasePasswordPattern.MatchString(value) {
			return fmt.Errorf("use at least 20 characters containing only letters, numbers, underscore, or hyphen")
		}
	case "caddy_password":
		if len(value) < 20 {
			return fmt.Errorf("use a password with at least 20 characters")
		}
	case "ai_provider":
		if value == "" || value == "none" {
			return nil
		}
		if _, _, _, _, err := customAIProviderCheck(value); err != nil {
			return err
		}
	case "ai_api_key":
		if value != "" {
			return nil
		}
	}
	return nil
}

var databasePasswordPattern = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func customAIProviderCheck(provider string) (string, string, string, string, error) {
	return core.AIEnvironmentForWizard(provider)
}

func customConfigFromFields(fields []customField) (core.CustomDeployConfig, error) {
	values := make(map[string]string, len(fields))
	for _, field := range fields {
		values[field.key] = field.value
	}
	config := core.CustomDeployConfig{
		Domain: values["domain"], TemporalDomain: values["temporal_domain"],
		DockerHubUsername: values["dockerhub_user"], DockerHubToken: values["dockerhub_token"],
		PostgresUser: values["postgres_user"], PostgresPassword: values["postgres_password"],
		ClickHouseAppPassword: values["clickhouse_app_password"], ClickHouseAPISecret: values["clickhouse_api_password"], ClickHouseReaderPassword: values["clickhouse_reader_password"],
		RedisPassword: values["redis_password"], ObjectStorageUser: values["object_storage_user"], ObjectStoragePassword: values["object_storage_password"],
		CaddyUsername: values["caddy_username"], CaddyPassword: values["caddy_password"],
		AIProvider: values["ai_provider"], AIModel: values["ai_model"], AIBaseURL: values["ai_base_url"], AIAPIKey: values["ai_api_key"],
		AppImage: values["app_image"], NodeImage: values["node_image"], FeatureFlagsImage: values["feature_flags_image"],
	}
	if config.AIProvider != "" && config.AIProvider != "none" && (config.AIModel == "" || config.AIAPIKey == "") {
		return config, fmt.Errorf("AI provider requires a model and API key")
	}
	if !strings.Contains(config.AppImage, ":") || !strings.Contains(config.NodeImage, ":") || !strings.Contains(config.FeatureFlagsImage, ":") {
		return config, fmt.Errorf("all three image references must include a tag")
	}
	return config, nil
}

func RunCustom() error {
	root, err := findCustomRepoRoot()
	if err != nil {
		return err
	}
	if err := os.Chdir(root); err != nil {
		return fmt.Errorf("switch to repository root: %w", err)
	}
	model, err := newCustomModel()
	if err != nil {
		return err
	}
	p := tea.NewProgram(model, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		return err
	}
	return nil
}

func findCustomRepoRoot() (string, error) {
	directory, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(directory, "deploy.sh")); err == nil {
			if _, composeErr := os.Stat(filepath.Join(directory, "docker-compose.hobby.yml")); composeErr == nil {
				return directory, nil
			}
		}
		parent := filepath.Dir(directory)
		if parent == directory {
			return "", fmt.Errorf("custom deployment requires the feat/custom-posthog repository checkout")
		}
		directory = parent
	}
}
