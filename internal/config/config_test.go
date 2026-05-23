package config

import (
	"errors"
	"reflect"
	"slices"
	"strings"
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
[http]
port = 9090
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

	if cfg.HTTP.Port != 9090 {
		t.Errorf("port = %d, want 9090", cfg.HTTP.Port)
	}
}

func TestLoad_EnvOverridesTOML(t *testing.T) {
	tomlBody := `
[logging]
level = "debug"
[http]
port = 9090
`
	cfg, err := LoadWith("/etc/qsology.toml",
		fakeLookup(map[string]string{
			"QSOLOGY_LOGGING_LEVEL": "warn",
			"QSOLOGY_HTTP_PORT":     "7777",
		}),
		fakeReadFile(map[string]string{"/etc/qsology.toml": tomlBody}),
	)
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	if cfg.Logging.Level != "warn" {
		t.Errorf("logging.level should be warn from env, got %q", cfg.Logging.Level)
	}
	if cfg.HTTP.Port != 7777 {
		t.Errorf("port should be 7777 from env, got %d", cfg.HTTP.Port)
	}
}

func TestLoad_ConfigPathFromEnv(t *testing.T) {
	tomlBody := `
[logging]
level = "debug"
[http]
port = 12345
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
	if cfg.HTTP.Port != 12345 {
		t.Errorf("port = %d, want 12345 from QSOLOGY_CONFIG file", cfg.HTTP.Port)
	}
}

func TestDefaultsAreSecure(t *testing.T) {
	d := Defaults()
	if d.HTTP.BehindProxy {
		t.Errorf("BehindProxy should default to false")
	}
	if d.HTTP.Address != "127.0.0.1" {
		t.Errorf("Address default should bind to loopback, got %q", d.HTTP.Address)
	}
}

func TestValidate_PortRange(t *testing.T) {
	for _, tt := range []struct {
		port    int
		wantErr bool
	}{
		{0, true},
		{1, false},
		{8080, false},
		{65535, false},
		{65536, true},
		{-1, true},
	} {
		cfg := Defaults()
		cfg.HTTP.Port = tt.port
		err := validate(cfg)
		if (err != nil) != tt.wantErr {
			t.Errorf("port %d: err=%v, wantErr=%v", tt.port, err, tt.wantErr)
		}
	}
}

func TestValidate_BehindProxyRequiresTrustedProxies(t *testing.T) {
	cfg := Defaults()
	cfg.HTTP.BehindProxy = true
	err := validate(cfg)
	if err == nil {
		t.Fatal("behind_proxy=true with empty trusted_proxies must fail")
	}
	if !strings.Contains(err.Error(), "trusted_proxies") {
		t.Errorf("error should mention trusted_proxies, got %v", err)
	}
}

func TestValidate_TrustedProxiesRequiresBehindProxy(t *testing.T) {
	cfg := Defaults()
	cfg.HTTP.TrustedProxies = []string{"127.0.0.1/32"}
	if err := validate(cfg); err == nil {
		t.Error("trusted_proxies set without behind_proxy should fail")
	}
}

func TestValidate_TrustedProxyEntriesMustParse(t *testing.T) {
	cfg := Defaults()
	cfg.HTTP.BehindProxy = true
	cfg.HTTP.TrustedProxies = []string{"not-an-ip"}
	if err := validate(cfg); err == nil {
		t.Error("invalid CIDR/IP should fail")
	}

	cfg.HTTP.TrustedProxies = []string{"127.0.0.1/32", "10.0.0.0/8", "::1"}
	if err := validate(cfg); err != nil {
		t.Errorf("valid CIDRs and IPs should pass, got %v", err)
	}
}

func TestApplyEnv_TrustedProxiesSplitting(t *testing.T) {
	cfg := Defaults()
	cfg.HTTP.BehindProxy = true
	err := applyEnv(&cfg, fakeLookup(map[string]string{
		"QSOLOGY_HTTP_TRUSTED_PROXIES": "127.0.0.1/32, 10.0.0.0/8 ,  ,192.168.0.1",
	}))
	if err != nil {
		t.Fatalf("applyEnv failed: %v", err)
	}
	want := []string{"127.0.0.1/32", "10.0.0.0/8", "192.168.0.1"}
	if !slices.Equal(cfg.HTTP.TrustedProxies, want) {
		t.Errorf("got %v, want %v", cfg.HTTP.TrustedProxies, want)
	}
}

func TestDefaults_RequestLogging(t *testing.T) {
	d := Defaults()
	r := d.Logging.Requests
	if !r.Enabled {
		t.Error("request logging should default to enabled")
	}
	if r.LevelSuccess != "info" || r.LevelClientError != "warn" || r.LevelServerError != "error" {
		t.Errorf("default levels = (%q, %q, %q), want (info, warn, error)",
			r.LevelSuccess, r.LevelClientError, r.LevelServerError)
	}
	if !slices.Equal(r.DebugPaths, []string{"/static/**"}) {
		t.Errorf("default debug_paths = %v, want [/static/**]", r.DebugPaths)
	}
}

func TestLoad_RequestLogging_TOMLOverlay(t *testing.T) {
	tomlBody := `
[logging.requests]
enabled            = false
level_success      = "debug"
level_client_error = "info"
level_server_error = "warn"
debug_paths        = ["/static/**", "/healthz"]
`
	cfg, err := LoadWith("/etc/qsology.toml", fakeLookup(nil), fakeReadFile(map[string]string{
		"/etc/qsology.toml": tomlBody,
	}))
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	r := cfg.Logging.Requests
	if r.Enabled {
		t.Error("enabled should be false from TOML")
	}
	if r.LevelSuccess != "debug" || r.LevelClientError != "info" || r.LevelServerError != "warn" {
		t.Errorf("levels = (%q, %q, %q)", r.LevelSuccess, r.LevelClientError, r.LevelServerError)
	}
	if !slices.Equal(r.DebugPaths, []string{"/static/**", "/healthz"}) {
		t.Errorf("debug_paths = %v", r.DebugPaths)
	}
}

func TestLoad_RequestLogging_EnvOverridesTOML(t *testing.T) {
	tomlBody := `
[logging.requests]
level_success = "debug"
debug_paths   = ["/from-toml/"]
`
	cfg, err := LoadWith("/etc/qsology.toml",
		fakeLookup(map[string]string{
			"QSOLOGY_LOGGING_REQUESTS_ENABLED":            "false",
			"QSOLOGY_LOGGING_REQUESTS_LEVEL_SUCCESS":      "warn",
			"QSOLOGY_LOGGING_REQUESTS_LEVEL_CLIENT_ERROR": "error",
			"QSOLOGY_LOGGING_REQUESTS_LEVEL_SERVER_ERROR": "error",
			"QSOLOGY_LOGGING_REQUESTS_DEBUG_PATHS":        "/a/, /b/ ,  ,/c/",
		}),
		fakeReadFile(map[string]string{"/etc/qsology.toml": tomlBody}),
	)
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	r := cfg.Logging.Requests
	if r.Enabled {
		t.Error("enabled should be false from env")
	}
	if r.LevelSuccess != "warn" {
		t.Errorf("level_success = %q, want warn", r.LevelSuccess)
	}
	if !slices.Equal(r.DebugPaths, []string{"/a/", "/b/", "/c/"}) {
		t.Errorf("debug_paths = %v, want env-split list", r.DebugPaths)
	}
}

func TestValidate_RequestLoggingLevels(t *testing.T) {
	cfg := Defaults()
	cfg.Logging.Requests.LevelSuccess = "trace"
	if err := validate(cfg); err == nil {
		t.Error("invalid level should fail when enabled")
	}

	cfg = Defaults()
	cfg.Logging.Requests.Enabled = false
	cfg.Logging.Requests.LevelSuccess = "trace"
	if err := validate(cfg); err != nil {
		t.Errorf("invalid level should be ignored when disabled, got %v", err)
	}
}

func TestValidate_RequestLoggingDebugPathsMustBeAbsolute(t *testing.T) {
	cfg := Defaults()
	cfg.Logging.Requests.DebugPaths = []string{"static/"}
	err := validate(cfg)
	if err == nil {
		t.Fatal("relative debug_paths entry should fail")
	}
	if !strings.Contains(err.Error(), "debug_paths") {
		t.Errorf("error should mention debug_paths, got %v", err)
	}
}

func TestValidate_RequestLoggingDebugPathsMustBeValidGlobs(t *testing.T) {
	cfg := Defaults()
	// Unclosed character class — invalid doublestar pattern.
	cfg.Logging.Requests.DebugPaths = []string{"/static/[abc"}
	err := validate(cfg)
	if err == nil {
		t.Fatal("malformed glob should fail")
	}
	if !strings.Contains(err.Error(), "valid glob") {
		t.Errorf("error should mention valid glob, got %v", err)
	}

	// A few patterns we'd expect to accept.
	for _, g := range []string{"/static/**", "/healthz", "/api/*/internal/*", "/img/*.png"} {
		cfg := Defaults()
		cfg.Logging.Requests.DebugPaths = []string{g}
		if err := validate(cfg); err != nil {
			t.Errorf("glob %q should validate, got %v", g, err)
		}
	}
}

func TestApplyEnv_BadPortReturnsError(t *testing.T) {
	cfg := Defaults()
	if err := applyEnv(&cfg, fakeLookup(map[string]string{"QSOLOGY_HTTP_PORT": "abc"})); err == nil {
		t.Error("non-numeric port should error")
	}
}
