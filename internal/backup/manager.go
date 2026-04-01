package backup

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/l-ptrol/mhstudio-go/internal/config"
)

type BackupInfo struct {
	Name    string
	Path    string
	ModTime time.Time
	Content string
}

// ListBackups возвращает все бэкапы, отсортированные по дате (новые первыми)
func ListBackups() ([]BackupInfo, error) {
	files, err := filepath.Glob(filepath.Join(config.BackupDir, "*.yaml"))
	if err != nil {
		return nil, err
	}

	// Сортируем по времени модификации (новые первыми)
	sort.Slice(files, func(i, j int) bool {
		fi, _ := os.Stat(files[i])
		fj, _ := os.Stat(files[j])
		return fi.ModTime().After(fj.ModTime())
	})

	var backups []BackupInfo
	for _, f := range files {
		fi, _ := os.Stat(f)
		backups = append(backups, BackupInfo{
			Name:    filepath.Base(f),
			Path:    f,
			ModTime: fi.ModTime(),
		})
	}
	return backups, nil
}

// CreateBackup создаёт бэкап текущего конфига
func CreateBackup() error {
	realPath, err := filepath.EvalSymlinks(config.ConfigPath)
	if err != nil {
		return err
	}
	profName := strings.TrimSuffix(filepath.Base(realPath), ".yaml")
	ts := time.Now().Format("20060102_150405")
	backupPath := filepath.Join(config.BackupDir, profName+"_"+ts+".yaml")

	data, err := os.ReadFile(config.ConfigPath)
	if err != nil {
		return err
	}
	return os.WriteFile(backupPath, data, 0644)
}

// RestoreBackup восстанавливает конфиг из бэкапа
func RestoreBackup(filename string) error {
	path := filepath.Join(config.BackupDir, filepath.Base(filename))
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return os.WriteFile(config.ConfigPath, data, 0644)
}

// ViewBackup возвращает содержимое бэкапа
func ViewBackup(filename string) (string, error) {
	path := filepath.Join(config.BackupDir, filepath.Base(filename))
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

// DeleteBackup удаляет бэкап
func DeleteBackup(filename string) error {
	path := filepath.Join(config.BackupDir, filepath.Base(filename))
	return os.Remove(path)
}

// CleanBackups оставляет только N последних бэкапов
func CleanBackups(limit int) error {
	backups, err := ListBackups()
	if err != nil {
		return err
	}
	if len(backups) > limit {
		for _, b := range backups[limit:] {
			os.Remove(b.Path)
		}
	}
	return nil
}
