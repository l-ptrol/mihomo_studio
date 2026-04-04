package config

import (
	"os"
	"path/filepath"
	"runtime"
)

var (
	Port       = 8888
	RestartCmd = "xkeen -restart > /tmp/mihomo_last_restart.log 2>&1"
	UpdateCmd  = "/opt/bin/mhstudio -update"
	LogFile    = "/tmp/mihomo_last_restart.log"
	InitScript = "/opt/etc/init.d/S95mihomo-web"
	PidFile    = "/opt/var/run/mhstudio.pid"
)

var (
	ConfigDir   = "/opt/etc/mihomo"
	ConfigPath  string
	ProfilesDir string
	BackupDir   string
)

func Init() {
	if runtime.GOOS == "windows" {
		ConfigDir = "config"
		RestartCmd = "echo Restart not supported on Windows"
		UpdateCmd = "echo Update not supported on Windows"
		LogFile = "restart.log"
		InitScript = "init.sh"
		PidFile = "mhstudio.pid"
	}

	ConfigPath = filepath.Join(ConfigDir, "config.yaml")
	ProfilesDir = filepath.Join(ConfigDir, "profiles")
	BackupDir = filepath.Join(ConfigDir, "backup")

	os.MkdirAll(BackupDir, 0755)
	os.MkdirAll(ProfilesDir, 0755)

	// Если config.yaml существует и не симлинк — перемещаем в profiles
	if info, err := os.Lstat(ConfigPath); err == nil {
		if info.Mode()&os.ModeSymlink == 0 {
			defaultProf := filepath.Join(ProfilesDir, "default.yaml")
			os.Rename(ConfigPath, defaultProf)
			os.Symlink(defaultProf, ConfigPath)
		}
	} else {
		defProf := filepath.Join(ProfilesDir, "default.yaml")
		os.WriteFile(defProf, []byte("proxies: []\n"), 0644)
		os.Symlink(defProf, ConfigPath)
	}
}
