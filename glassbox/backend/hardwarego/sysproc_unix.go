//go:build !windows

package main

import "syscall"

func commandSysProcAttr() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{Setpgid: true}
}
