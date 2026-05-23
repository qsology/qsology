package config

import (
	"errors"
	"fmt"
	"net/netip"
	neturl "net/url"
	"os"
	"strconv"
	"strings"

	"github.com/BurntSushi/toml"
	"github.com/bmatcuk/doublestar/v4"
)

// Config struct for all configuration the app uses
type Config struct {
	Logging LoggingConfig `toml:"logging"`
	HTTP    HTTPConfig    `toml:"http"`
}

// LoggingConfig struct for Logging configuration
type LoggingConfig struct {
	Level    string               `toml:"level"`
	Format   string               `toml:"format"`
	Requests RequestLoggingConfig `toml:"requests"`
}

// RequestLoggingConfig controls the per-request HTTP access log.
//
// Enabled toggles the middleware as a whole. The three Level*'s map
// response status classes to slog levels.
// DebugPaths is a list of doublestar globs (for example "/static/**", "/healthz")
// whose successful (2xx/3xx) responses are demoted to DEBUG; 4xx and 5xx
// on those paths still log at their configured LevelClientError /
// LevelServerError.
// `*` matches within a path segment, `**` crosses segments.
type RequestLoggingConfig struct {
	Enabled          bool     `toml:"enabled"`
	LevelSuccess     string   `toml:"level_success"`
	LevelClientError string   `toml:"level_client_error"`
	LevelServerError string   `toml:"level_server_error"`
	DebugPaths       []string `toml:"debug_paths"`
}

// HTTPConfig struct for HTTP Server configuration
type HTTPConfig struct {
	Address        string   `toml:"address"`
	Port           int      `toml:"port"`
	BehindProxy    bool     `toml:"behind_proxy"`
	TrustedProxies []string `toml:"trusted_proxies"`
	PublicURL      string   `toml:"public_url"`
}

// Defaults Default configuration
func Defaults() Config {
	return Config{
		Logging: LoggingConfig{
			Level:  "info",
			Format: "json",
			Requests: RequestLoggingConfig{
				Enabled:          true,
				LevelSuccess:     "info",
				LevelClientError: "warn",
				LevelServerError: "error",
				DebugPaths:       []string{"/static/**"},
			},
		},
		HTTP: HTTPConfig{
			Address:     "127.0.0.1",
			Port:        8080,
			BehindProxy: false,
		},
	}
}

// Lookup like os.LookupEnv
type Lookup func(key string) (string, bool)

// Load config
func Load(configPath string) (Config, error) {
	return LoadWith(configPath, os.LookupEnv, os.ReadFile)
}

// LoadWith is what Load uses, allowing testing to work without touching env vars or filesystem
func LoadWith(configPath string, lookup Lookup, readFile func(string) ([]byte, error)) (Config, error) {
	// Load defaults
	cfg := Defaults()

	path := configPath
	// If path isn't set check for the env var
	if path == "" {
		if v, ok := lookup("QSOLOGY_CONFIG_FILE"); ok {
			path = v
		}
	}
	// if path is set load the toml file
	if path != "" {
		if err := applyTOMLFile(&cfg, path, readFile); err != nil {
			return Config{}, fmt.Errorf("config file %q: %w", path, err)
		}
	}
	// Read env vars
	if err := applyEnv(&cfg, lookup); err != nil {
		return Config{}, fmt.Errorf("environment variable lookup: %w", err)
	}

	// TODO resolve files for secrets here if set to file://

	// Return error if config doesnt validate
	if err := validate(cfg); err != nil {
		return Config{}, err
	}
	// Return config
	return cfg, nil
}

// Keys that are forbidden to be in the toml file
var forbiddenTOMLKeys = [][]string{
	{"database", "password"},
}

// Read the config from TOML file
func applyTOMLFile(cfg *Config, path string, readFile func(string) ([]byte, error)) error {
	data, err := readFile(path)
	if err != nil {
		return err
	}
	meta, err := toml.Decode(string(data), cfg)
	if err != nil {
		return err
	}
	for _, forbidden := range forbiddenTOMLKeys {
		if meta.IsDefined(forbidden...) {
			return fmt.Errorf("forbidden TOML key %q found - use the QSOLOGY_* environment variable or a file:// reference for this secret", strings.Join(forbidden, "."))
		}
	}
	return nil
}

func applyEnv(cfg *Config, lookup Lookup) error {
	// Logging
	if v, ok := lookup("QSOLOGY_LOGGING_LEVEL"); ok {
		cfg.Logging.Level = v
	}
	if v, ok := lookup("QSOLOGY_LOGGING_FORMAT"); ok {
		cfg.Logging.Format = v
	}
	if err := envBool(lookup, "QSOLOGY_LOGGING_REQUESTS_ENABLED", &cfg.Logging.Requests.Enabled); err != nil {
		return err
	}
	if v, ok := lookup("QSOLOGY_LOGGING_REQUESTS_LEVEL_SUCCESS"); ok {
		cfg.Logging.Requests.LevelSuccess = v
	}
	if v, ok := lookup("QSOLOGY_LOGGING_REQUESTS_LEVEL_CLIENT_ERROR"); ok {
		cfg.Logging.Requests.LevelClientError = v
	}
	if v, ok := lookup("QSOLOGY_LOGGING_REQUESTS_LEVEL_SERVER_ERROR"); ok {
		cfg.Logging.Requests.LevelServerError = v
	}
	if v, ok := lookup("QSOLOGY_LOGGING_REQUESTS_DEBUG_PATHS"); ok {
		cfg.Logging.Requests.DebugPaths = splitList(v)
	}
	// HTTP
	if v, ok := lookup("QSOLOGY_HTTP_ADDRESS"); ok {
		cfg.HTTP.Address = v
	}
	if v, ok := lookup("QSOLOGY_HTTP_PORT"); ok {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("QSOLOGY_HTTP_PORT: %w", err)
		}
		cfg.HTTP.Port = n
	}
	if err := envBool(lookup, "QSOLOGY_HTTP_BEHIND_PROXY", &cfg.HTTP.BehindProxy); err != nil {
		return err
	}
	if v, ok := lookup("QSOLOGY_HTTP_TRUSTED_PROXIES"); ok {
		cfg.HTTP.TrustedProxies = splitList(v)
	}
	if v, ok := lookup("QSOLOGY_HTTP_PUBLIC_URL"); ok {
		cfg.HTTP.PublicURL = v
	}
	return nil
}

func envBool(lookup Lookup, key string, dst *bool) error {
	v, ok := lookup(key)
	if !ok {
		return nil
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return fmt.Errorf("%s: invalid boolean %q: %w", key, v, err)
	}
	*dst = b
	return nil
}

func splitList(v string) []string {
	var out []string
	for _, part := range strings.Split(v, ",") {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	return out
}

func validate(cfg Config) error {
	// Logging
	//// Level
	switch strings.ToLower(cfg.Logging.Level) {
	case "debug", "info", "warn", "error":
	default:
		return fmt.Errorf("logging.level %q invalid (expecting debug, info, warn, error)", cfg.Logging.Level)
	}
	//// Format
	switch strings.ToLower(cfg.Logging.Format) {
	case "text", "json":
	default:
		return fmt.Errorf("logging.format %q invalid (expecting text, json)", cfg.Logging.Format)
	}
	//// Request log levels (only checked when the middleware is enabled;
	//// when disabled the strings are inert, and we don't punish a misconfig
	//// that has no runtime effect)
	if cfg.Logging.Requests.Enabled {
		for _, lvl := range []struct{ key, val string }{
			{"logging.requests.level_success", cfg.Logging.Requests.LevelSuccess},
			{"logging.requests.level_client_error", cfg.Logging.Requests.LevelClientError},
			{"logging.requests.level_server_error", cfg.Logging.Requests.LevelServerError},
		} {
			switch strings.ToLower(lvl.val) {
			case "debug", "info", "warn", "warning", "error":
			default:
				return fmt.Errorf("%s %q invalid (expecting debug, info, warn, error)", lvl.key, lvl.val)
			}
		}
		for _, p := range cfg.Logging.Requests.DebugPaths {
			if !strings.HasPrefix(p, "/") {
				// URL paths always start with "/", so a pattern without one
				// would never match anything — almost certainly a typo.
				return fmt.Errorf("logging.requests.debug_paths entry %q must start with /", p)
			}
			if !doublestar.ValidatePattern(p) {
				return fmt.Errorf("logging.requests.debug_paths entry %q is not a valid glob", p)
			}
		}
	}

	// HTTP
	//// Port
	if cfg.HTTP.Port < 1 || cfg.HTTP.Port > 65535 {
		return fmt.Errorf("http.port %d out of range (1-65535)", cfg.HTTP.Port)
	}
	//// Public URL
	if cfg.HTTP.PublicURL != "" {
		u, err := neturl.Parse(cfg.HTTP.PublicURL)
		if err != nil {
			return fmt.Errorf("http.public_url: %w", err)
		}
		if !u.IsAbs() || (u.Scheme != "http" && u.Scheme != "https") {
			return fmt.Errorf("http.public_url must be an absolute http/https URL, got %q", cfg.HTTP.PublicURL)
		}
		if u.Host == "" {
			return errors.New("http.public_url must include a host")
		}
	}
	//// Proxies
	////// X-F-For spoofing is a problem. If config is ambiguous, treat as incorrect
	if cfg.HTTP.BehindProxy && len(cfg.HTTP.TrustedProxies) == 0 {
		return errors.New("http.behind_proxy is true but http.trusted_proxies is empty: X-Forwarded-* would be trusted from any source")
	}
	if !cfg.HTTP.BehindProxy && len(cfg.HTTP.TrustedProxies) > 0 {
		return errors.New("http.trusted_proxies is set but http.behind_proxy is false: enable behind_proxy or clear trusted_proxies")
	}
	for _, p := range cfg.HTTP.TrustedProxies {
		if err := validateProxyEntry(p); err != nil {
			return fmt.Errorf("http.trusted_proxies entry %q: %w", p, err)
		}
	}
	// Otherwise passes
	return nil
}

func validateProxyEntry(s string) error {
	if _, err := netip.ParsePrefix(s); err == nil {
		return nil
	}
	if _, err := netip.ParseAddr(s); err == nil {
		return nil
	}
	return errors.New("not a valid IP or CIDR")
}
