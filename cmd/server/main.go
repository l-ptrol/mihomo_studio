package main

import (
	"flag"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"syscall"

	"github.com/l-ptrol/mhstudio-go/internal/config"
	"github.com/l-ptrol/mhstudio-go/internal/handler"
	"github.com/l-ptrol/mhstudio-go/web"
)

var Version = "2.2.31"

func main() {
	fStop := flag.Bool("stop", false, "Stop the server")
	fRestart := flag.Bool("restart", false, "Restart the server")
	fUpdate := flag.Bool("update", false, "Update the service")
	fVersion := flag.Bool("v", false, "Show version")
	flag.Parse()

	if *fVersion || (len(os.Args) > 1 && os.Args[1] == "-version") {
		fmt.Printf("Mihomo Studio v%s\n", Version)
		return
	}

	if *fStop {
		stopService()
		return
	}

	if *fRestart {
		runCmd(config.InitScript + " restart")
		return
	}

	if *fUpdate {
		fmt.Println(">>> Starting update...")
		if runtime.GOOS == "windows" {
			fmt.Println("Error: Update via script is not supported on Windows. Please update manually.")
		} else {
			runCmd("wget -O - https://raw.githubusercontent.com/l-ptrol/mihomo_studio/test-go/install.sh | sh")
		}
		return
	}

	// Default or explicit -start
	startServer()
}

func startServer() {
	config.Init()

	tmplData, err := web.TemplateFS.ReadFile("templates/index.html")
	if err != nil {
		log.Fatalf("Ошибка: %v", err)
	}

	h := handler.New(Version)
	h.LoadTemplate(string(tmplData))

	addr := fmt.Sprintf(":%d", config.Port)
	srv := &http.Server{
		Addr:    addr,
		Handler: h,
	}

	// Write PID file
	err = ioutil.WriteFile(config.PidFile, []byte(strconv.Itoa(os.Getpid())), 0644)
	if err != nil {
		log.Printf("Предупреждение: не удалось записать PID файл: %v", err)
	}

	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Остановка сервера...")
		os.Remove(config.PidFile)
		srv.Close()
	}()

	log.Printf("Mihomo Studio v%s запущен на http://0.0.0.0%s", Version, addr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Ошибка сервера: %v", err)
	}
}

func stopService() {
	data, err := ioutil.ReadFile(config.PidFile)
	if err != nil {
		// FALLBACK: pkill if no pid file
		exec.Command("pkill", "-f", "mhstudio").Run()
		fmt.Println("Service stop requested (via pkill).")
		return
	}
	pid, _ := strconv.Atoi(strings.TrimSpace(string(data)))
	process, err := os.FindProcess(pid)
	if err == nil {
		process.Signal(syscall.SIGTERM)
		fmt.Printf("Stopped Mihomo Studio (PID %d)\n", pid)
	}
	os.Remove(config.PidFile)
}

func runCmd(c string) {
	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.Command("cmd", "/C", c)
	} else {
		cmd = exec.Command("sh", "-c", c)
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Run()
}

