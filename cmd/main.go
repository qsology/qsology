package main

import (
	"context"
	"fmt"
	"os"

	"github.com/urfave/cli/v3"
)

// version set at build time with -ldflags="-X main.buildVersion=0.1"
var buildVersion = "development"

// Root command using urfave/cli
func rootCommand() *cli.Command {
	return &cli.Command{
		Name:    "QSOlogy",
		Usage:   "QSOlogy is a hamradio QSO logging app",
		Version: buildVersion,
		Flags: []cli.Flag{
			&cli.StringFlag{
				Name:    "config",
				Usage:   "Load configuration from `FILE`",
				Sources: cli.EnvVars("QSOLOGY_CONFIG_FILE"),
				Aliases: []string{
					"conf",
					"f",
				},
			},
		},
		Commands: []*cli.Command{
			validateConfigCommand(),
			serveCommand(),
		},
	}
}

func main() {
	if err := rootCommand().Run(context.Background(), os.Args); err != nil {
		fmt.Printf("QSOlogy: %v\n", err)
		os.Exit(1)
	}
}
