package config

import (
	"errors"
	"reflect"
	"testing"
)

func fakeLookup(env map[string]string) Lookup {
	return func(key string) (string, bool) {
		v, ok := env[key]
		return v, ok
	}
}

func fakeReadFile(files map[string]string) func(string) ([]byte, error) {
	return func(path string) ([]byte, error) {
		data, ok := files[path]
		if !ok {
			return nil, errors.New("not found: " + path)
		}
		return []byte(data), nil
	}
}

func TestLoad_DefaultsOnly(t *testing.T) {
	cfg, err := LoadWith("", fakeLookup(nil), fakeReadFile(nil))
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	if !reflect.DeepEqual(cfg, Defaults()) {
		t.Errorf("expected defaults, got %+v", cfg)
	}
}

func TestLoad_TOMLOverlay(t *testing.T) {
	tomlBody := `
[logging]
level = "debug"
format = "text"
`
	cfg, err := LoadWith("/etc/qsology.toml", fakeLookup(nil), fakeReadFile(map[string]string{
		"/etc/qsology.toml": tomlBody,
	}))
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}

	if cfg.Logging.Level != "debug" {
		t.Errorf("logging.level = %q, want debug", cfg.Logging.Level)
	}
	if cfg.Logging.Format != "text" {
		t.Errorf("logging.format = %q, want text", cfg.Logging.Format)
	}
}

func TestLoad_EnvOverridesTOML(t *testing.T) {
	tomlBody := `
[logging]
level = "debug"
`
	cfg, err := LoadWith("/etc/qsology.toml",
		fakeLookup(map[string]string{
			"QSOLOGY_LOGGING_LEVEL": "warn",
		}),
		fakeReadFile(map[string]string{"/etc/qsology.toml": tomlBody}),
	)
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	if cfg.Logging.Level != "warn" {
		t.Errorf("logging.level should be warn from env, got %q", cfg.Logging.Level)
	}
}

func TestLoad_ConfigPathFromEnv(t *testing.T) {
	tomlBody := `
[logging]
level = "debug"
`
	cfg, err := LoadWith("",
		fakeLookup(map[string]string{"QSOLOGY_CONFIG_FILE": "/somewhere/qsology.toml"}),
		fakeReadFile(map[string]string{"/somewhere/qsology.toml": tomlBody}),
	)
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	if cfg.Logging.Level != "debug" {
		t.Errorf("logging.level should be debug from env, got %q", cfg.Logging.Level)
	}
}
