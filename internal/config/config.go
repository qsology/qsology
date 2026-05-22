package config

import (
	"fmt"
	"os"
	"strings"

	"github.com/BurntSushi/toml"
)

// Config struct for all configuration the app uses
type Config struct {
	Logging LoggingConfig `toml:"logging"`
}

// LoggingConfig struct for Logging configuration
type LoggingConfig struct {
	Level  string `toml:"level"`
	Format string `toml:"format"`
}

// Defaults Default configuration
func Defaults() Config {
	return Config{
		Logging: LoggingConfig{
			Level:  "info",
			Format: "json",
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
	if v, ok := lookup("QSOLOGY_LOGGING_LEVEL"); ok {
		cfg.Logging.Level = v
	}
	if v, ok := lookup("QSOLOGY_LOGGING_FORMAT"); ok {
		cfg.Logging.Format = v
	}
	return nil
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
	// Otherwise passes
	return nil
}
