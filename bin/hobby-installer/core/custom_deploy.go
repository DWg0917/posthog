package core

import (
	"bufio"
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"
)

type CustomDeployConfig struct {
	Domain, TemporalDomain                                               string
	DockerHubUsername, DockerHubToken                                    string
	PostgresUser, PostgresPassword                                       string
	ClickHouseAppPassword, ClickHouseAPISecret, ClickHouseReaderPassword string
	RedisPassword, ObjectStorageUser, ObjectStoragePassword              string
	CaddyUsername, CaddyPassword                                         string
	AIProvider, AIModel, AIBaseURL, AIAPIKey                             string
	AppImage, NodeImage, FeatureFlagsImage                               string
	TemporalWorkerProfiles                                               []string
}

func GenerateDeploymentPassword() (string, error) {
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return "", fmt.Errorf("generate password: %w", err)
	}
	return hex.EncodeToString(secret), nil
}

func RunCustomDeployment(config CustomDeployConfig) error {
	if err := RequireRoot("install Docker and deploy PostHog"); err != nil {
		return err
	}
	logger := GetLogger()
	logger.WriteString("Step 1/6: checking Docker and Compose\n")
	if !IsDockerInstalled() {
		if err := InstallDocker(); err != nil {
			return err
		}
	}
	if !IsDockerRunning() {
		if err := StartDockerDaemon(); err != nil {
			return err
		}
	}
	if _, err := exec.LookPath("docker-compose"); err != nil {
		if _, composeErr := RunCommand("docker", "compose", "version"); composeErr != nil {
			if err := InstallDockerCompose(); err != nil {
				return err
			}
		}
	}

	logger.WriteString("Step 2/6: signing in to Docker Hub\n")
	if config.DockerHubUsername == "" || config.DockerHubToken == "" {
		return fmt.Errorf("Docker Hub username and access token are required")
	}
	login := exec.Command("docker", "login", "--username", config.DockerHubUsername, "--password-stdin")
	login.Stdin = strings.NewReader(config.DockerHubToken + "\n")
	var loginOutput bytes.Buffer
	login.Stdout = &loginOutput
	login.Stderr = &loginOutput
	if err := login.Run(); err != nil {
		return fmt.Errorf("Docker Hub login failed: %s", strings.TrimSpace(loginOutput.String()))
	}

	logger.WriteString("Step 3/6: creating Caddy Basic Auth hash\n")
	hashCmd := exec.Command("docker", "run", "--rm", "-i", "caddy:2", "caddy", "hash-password")
	hashCmd.Stdin = strings.NewReader(config.CaddyPassword + "\n")
	hashOutput, err := hashCmd.Output()
	if err != nil {
		return fmt.Errorf("could not create Caddy password hash: %w", err)
	}
	passwordHash := strings.TrimSpace(string(hashOutput))
	if !strings.HasPrefix(passwordHash, "$2a$") && !strings.HasPrefix(passwordHash, "$2b$") {
		return fmt.Errorf("Caddy returned an unexpected password hash")
	}

	logger.WriteString("Step 4/6: writing private deployment configuration\n")
	if err := createDatabaseCertificates(config.Domain); err != nil {
		return err
	}
	if err := writeCustomDeploymentFiles(config, passwordHash); err != nil {
		return err
	}

	logger.WriteString("Step 5/6: validating Docker Compose configuration\n")
	if _, err := RunCommand("docker", "compose", "--env-file", ".env", "-f", "docker-compose.hobby.yml", "config", "--quiet"); err != nil {
		return fmt.Errorf("compose configuration is invalid: %w", err)
	}

	logger.WriteString("Step 6/6: deploying PostHog (image pulls and database migrations can take a while)\n")
	command := exec.Command("bash", "./deploy.sh")
	command.Stdout = logger
	command.Stderr = logger
	if err := command.Run(); err != nil {
		return fmt.Errorf("deployment failed: %w; see hobby-installer.log for details", err)
	}
	return nil
}

func createDatabaseCertificates(domain string) error {
	privateKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return fmt.Errorf("generate database TLS key: %w", err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return fmt.Errorf("generate database TLS serial: %w", err)
	}
	now := time.Now()
	template := x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: domain},
		DNSNames:              []string{domain},
		NotBefore:             now.Add(-5 * time.Minute),
		NotAfter:              now.AddDate(5, 0, 0),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, &template, &template, &privateKey.PublicKey, privateKey)
	if err != nil {
		return fmt.Errorf("create database TLS certificate: %w", err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		return fmt.Errorf("encode database TLS key: %w", err)
	}
	certificate := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER})
	key := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	if err := os.MkdirAll("share/database-certs", 0755); err != nil {
		return err
	}
	if err := os.WriteFile("share/database-certs/server.crt", certificate, 0644); err != nil {
		return fmt.Errorf("write database CA certificate: %w", err)
	}
	for _, target := range []struct {
		name string
		uid  int
	}{{"postgres", 70}, {"clickhouse", 101}} {
		directory := "share/database-certs/" + target.name
		if err := os.MkdirAll(directory, 0750); err != nil {
			return err
		}
		if err := os.WriteFile(directory+"/server.crt", certificate, 0644); err != nil {
			return err
		}
		if err := os.WriteFile(directory+"/server.key", key, 0400); err != nil {
			return err
		}
		if err := os.Chown(directory, target.uid, target.uid); err != nil {
			return fmt.Errorf("set %s TLS directory owner: %w", target.name, err)
		}
		if err := os.Chown(directory+"/server.crt", target.uid, target.uid); err != nil {
			return err
		}
		if err := os.Chown(directory+"/server.key", target.uid, target.uid); err != nil {
			return err
		}
		if err := os.Chmod(directory, 0500); err != nil {
			return err
		}
	}
	return nil
}

func writeCustomDeploymentFiles(config CustomDeployConfig, passwordHash string) error {
	if _, err := os.Stat(".env"); err == nil {
		return fmt.Errorf(".env already exists; refusing to overwrite existing deployment configuration")
	} else if !os.IsNotExist(err) {
		return err
	}
	secret, err := GenerateSecret()
	if err != nil {
		return err
	}
	encryptionKey, err := GenerateEncryptionKey()
	if err != nil {
		return err
	}
	defaultPassword, err := GenerateDeploymentPassword()
	if err != nil {
		return err
	}
	values := map[string]string{
		"DOMAIN":                           config.Domain,
		"SITE_URL":                         "https://" + config.Domain,
		"CADDY_HOST":                       config.Domain,
		"TLS_BLOCK":                        "",
		"POSTHOG_SECRET":                   secret,
		"ENCRYPTION_SALT_KEYS":             encryptionKey,
		"POSTHOG_APP_TAG":                  imageTag(config.AppImage),
		"POSTHOG_NODE_TAG":                 imageTag(config.NodeImage),
		"POSTHOG_APP_IMAGE":                config.AppImage,
		"POSTHOG_NODE_IMAGE":               config.NodeImage,
		"POSTHOG_FEATURE_FLAGS_IMAGE":      config.FeatureFlagsImage,
		"REGISTRY_URL":                     "diweigao/my-posthog-single",
		"BUILD_LOCAL_IMAGES":               "0",
		"SERVICE_ENV_FILE":                 ".env.services.runtime",
		"POSTGRES_USER":                    config.PostgresUser,
		"POSTGRES_PASSWORD":                config.PostgresPassword,
		"CLICKHOUSE_DEFAULT_PASSWORD":      defaultPassword,
		"CLICKHOUSE_API_PASSWORD":          config.ClickHouseAPISecret,
		"CLICKHOUSE_APP_PASSWORD":          config.ClickHouseAppPassword,
		"CLICKHOUSE_DICT_READER_PASSWORD":  config.ClickHouseReaderPassword,
		"POSTGRES_PORT":                    "5432",
		"CLICKHOUSE_HTTP_PORT":             "8123",
		"CLICKHOUSE_NATIVE_PORT":           "9000",
		"POSTGRES_BIND_ADDRESS":            "0.0.0.0",
		"CLICKHOUSE_BIND_ADDRESS":          "0.0.0.0",
		"TEMPORAL_UI_DOMAIN":               config.TemporalDomain,
		"TEMPORAL_UI_USERNAME":             config.CaddyUsername,
		"TEMPORAL_UI_PASSWORD_HASH":        passwordHash,
		"OBJECT_STORAGE_ACCESS_KEY_ID":     config.ObjectStorageUser,
		"OBJECT_STORAGE_SECRET_ACCESS_KEY": config.ObjectStoragePassword,
		"REDIS_PASSWORD":                   config.RedisPassword,
		"REDIS_URL":                        "redis://:" + config.RedisPassword + "@redis7:6379/",
		"INGESTION_GENERAL_REPLICAS":       "4",
		"INGESTION_TOPIC_PARTITIONS":       "8",
		"CLICKHOUSE_KAFKA_CONSUMERS":       "8",
		"COMPOSE_PROFILES":                 strings.Join(config.TemporalWorkerProfiles, ","),
	}
	if config.AIProvider != "" && config.AIProvider != "none" {
		provider := strings.ToLower(config.AIProvider)
		keyName, baseName, modelsName, defaultBase, err := aiEnvironment(provider)
		if err != nil {
			return err
		}
		if config.AIAPIKey == "" || config.AIModel == "" {
			return fmt.Errorf("AI provider requires both an API key and model")
		}
		values["AI_PROVIDER"] = provider
		values["LLM_PROVIDER"] = provider
		values["AI_MODEL"] = config.AIModel
		values["AI_SUPPORTED_MODELS"] = config.AIModel
		values[modelsName] = config.AIModel
		values[keyName] = config.AIAPIKey
		if config.AIBaseURL != "" {
			values[baseName] = config.AIBaseURL
		} else if defaultBase != "" {
			values[baseName] = defaultBase
		}
	}
	var env strings.Builder
	for key, value := range values {
		fmt.Fprintf(&env, "%s=%s\n", key, quoteComposeEnv(value))
	}
	if err := os.WriteFile(".env", []byte(env.String()), 0600); err != nil {
		return fmt.Errorf("write .env: %w", err)
	}

	serviceData, err := os.ReadFile(".env.services")
	if err != nil {
		return fmt.Errorf("read .env.services template: %w", err)
	}
	serviceValues := map[string]string{
		"DATABASE_URL":                     fmt.Sprintf("postgres://%s:%s@db:5432/posthog", config.PostgresUser, config.PostgresPassword),
		"PGUSER":                           config.PostgresUser,
		"PGPASSWORD":                       config.PostgresPassword,
		"CLICKHOUSE_API_PASSWORD":          config.ClickHouseAPISecret,
		"CLICKHOUSE_APP_PASSWORD":          config.ClickHouseAppPassword,
		"CLICKHOUSE_DICT_READER_PASSWORD":  config.ClickHouseReaderPassword,
		"CLICKHOUSE_BILLING_USER":          "app",
		"CLICKHOUSE_BILLING_PASSWORD":      config.ClickHouseAppPassword,
		"OBJECT_STORAGE_ACCESS_KEY_ID":     config.ObjectStorageUser,
		"OBJECT_STORAGE_SECRET_ACCESS_KEY": config.ObjectStoragePassword,
		"REDIS_URL":                        "redis://:" + config.RedisPassword + "@redis7:6379/",
	}
	runtimeData, err := replaceEnvValues(string(serviceData), serviceValues)
	if err != nil {
		return err
	}
	if err := os.WriteFile(".env.services.runtime", []byte(runtimeData), 0600); err != nil {
		return fmt.Errorf("write runtime service credentials: %w", err)
	}
	return nil
}

func imageTag(image string) string {
	_, tag, found := strings.Cut(image, ":")
	if found {
		return tag
	}
	return "latest"
}

func quoteComposeEnv(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "\\'") + "'"
}

func replaceEnvValues(content string, values map[string]string) (string, error) {
	var output strings.Builder
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := scanner.Text()
		key, _, found := strings.Cut(line, "=")
		if found {
			if value, replace := values[key]; replace {
				line = key + "=" + value
				delete(values, key)
			}
		}
		output.WriteString(line + "\n")
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}
	if len(values) > 0 {
		return "", fmt.Errorf("service environment template is missing required key(s): %s", strings.Join(sortedKeys(values), ", "))
	}
	return output.String(), nil
}

func aiEnvironment(provider string) (key, base, models, defaultBase string, err error) {
	switch provider {
	case "glm":
		return "GLM_API_KEY", "GLM_BASE_URL", "GLM_SUPPORTED_MODELS", "https://open.bigmodel.cn/api/coding/paas/v4", nil
	case "mimo":
		return "MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_SUPPORTED_MODELS", "https://token-plan-cn.xiaomimimo.com/v1", nil
	case "qwen":
		return "QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_SUPPORTED_MODELS", "https://dashscope.aliyuncs.com/compatible-mode/v1", nil
	case "custom", "openai", "anthropic":
		prefix := strings.ToUpper(provider)
		if provider == "custom" {
			return "CUSTOM_LLM_API_KEY", "CUSTOM_LLM_BASE_URL", "CUSTOM_LLM_MODELS", "", nil
		}
		return prefix + "_API_KEY", prefix + "_BASE_URL", prefix + "_SUPPORTED_MODELS", "", nil
	default:
		return "", "", "", "", fmt.Errorf("unsupported AI provider %q", provider)
	}
}

func AIEnvironmentForWizard(provider string) (string, string, string, string, error) {
	return aiEnvironment(strings.ToLower(strings.TrimSpace(provider)))
}

func sortedKeys(values map[string]string) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

var safeDatabaseUsername = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,62}$`)

func ValidateDatabaseUsername(username string) error {
	if !safeDatabaseUsername.MatchString(username) {
		return fmt.Errorf("database username must start with a letter or underscore and contain only letters, digits, and underscores")
	}
	return nil
}
