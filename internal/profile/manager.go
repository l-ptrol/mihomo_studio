package profile

import (
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/l-ptrol/mhstudio-go/internal/config"
)

type ProfileInfo struct {
	Name    string
	Path    string
	Content string
}

// ListProfiles возвращает список всех профилей
func ListProfiles() ([]ProfileInfo, error) {
	files, err := filepath.Glob(filepath.Join(config.ProfilesDir, "*.yaml"))
	if err != nil {
		return nil, err
	}
	sort.Strings(files)

	var profiles []ProfileInfo
	for _, f := range files {
		name := strings.TrimSuffix(filepath.Base(f), ".yaml")
		profiles = append(profiles, ProfileInfo{Name: name, Path: f})
	}
	return profiles, nil
}

// GetCurrentProfile возвращает имя текущего активного профиля
func GetCurrentProfile() string {
	real, err := filepath.EvalSymlinks(config.ConfigPath)
	if err != nil {
		return ""
	}
	return strings.TrimSuffix(filepath.Base(real), ".yaml")
}

// SwitchProfile переключает активный профиль
func SwitchProfile(name string) error {
	target := filepath.Join(config.ProfilesDir, name+".yaml")
	if _, err := os.Stat(target); err != nil {
		return &OpError{Msg: "Profile not found"}
	}

	// Удаляем текущий симлинк
	os.Remove(config.ConfigPath)
	return os.Symlink(target, config.ConfigPath)
}

// AddProfile создаёт новый профиль
func AddProfile(name, content string) error {
	target := filepath.Join(config.ProfilesDir, name+".yaml")
	if _, err := os.Stat(target); err == nil {
		return &OpError{Msg: "Профиль с таким именем уже существует"}
	}
	return os.WriteFile(target, []byte(content), 0644)
}

// DeleteProfile удаляет профиль
func DeleteProfile(name string) error {
	target := filepath.Join(config.ProfilesDir, name+".yaml")

	// Нельзя удалить активный профиль
	realCurr, _ := filepath.EvalSymlinks(config.ConfigPath)
	realTarget, _ := filepath.EvalSymlinks(target)
	if realTarget == realCurr {
		return &OpError{Msg: "Нельзя удалить активный профиль. Сначала переключитесь на другой."}
	}

	return os.Remove(target)
}

// GetProfileContent возвращает содержимое профиля
func GetProfileContent(name string) (string, error) {
	target := filepath.Join(config.ProfilesDir, name+".yaml")
	data, err := os.ReadFile(target)
	if err != nil {
		return "", &OpError{Msg: "Profile not found"}
	}
	return string(data), nil
}

type OpError struct {
	Msg string
}

func (e *OpError) Error() string {
	return e.Msg
}
