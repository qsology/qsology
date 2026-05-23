package main

import (
	"context"
	"fmt"
	"log/slog"

	"github.com/qsology/qsology/internal/config"
	"github.com/qsology/qsology/internal/logging"
	"github.com/urfave/cli/v3"
)

// Bootstrap and load the config
func bootstrap(cmd *cli.Command) (config.Config, *slog.Logger, error) {
	cfg, err := config.Load(cmd.String("config"))
	if err != nil {
		return config.Config{}, nil, fmt.Errorf("failed to load config: %w", err)
	}

	logger, err := logging.New(cfg.Logging)
	if err != nil {
		return config.Config{}, nil, fmt.Errorf("failed to initialize logger: %w", err)
	}

	return cfg, logger, nil
}

// Command to validate configuration
func validateConfigCommand() *cli.Command {
	return &cli.Command{
		Name:  "test",
		Usage: "test configuration",
		Aliases: []string{
			"validate",
		},
		Action: validateConfig,
	}
}

// Validate the configuration
func validateConfig(_ context.Context, cmd *cli.Command) error {
	_, _, err := bootstrap(cmd)
	if err != nil {
		return fmt.Errorf("configuration validation failed: %w", err)
	}
	return nil
}
