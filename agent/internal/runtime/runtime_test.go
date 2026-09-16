package runtime

import "testing"

func TestHostLimits(t *testing.T) {
	limits := hostLimits(ContainerSpec{MemoryMB: 1024, CPUs: 1.5})
	if got := limits["Memory"]; got != int64(1024*1024*1024) {
		t.Errorf("Memory = %v", got)
	}
	if limits["MemorySwap"] != limits["Memory"] {
		t.Errorf("MemorySwap = %v, want the memory limit", limits["MemorySwap"])
	}
	if got := limits["NanoCpus"]; got != int64(1_500_000_000) {
		t.Errorf("NanoCpus = %v", got)
	}
	policy := limits["RestartPolicy"].(map[string]any)
	if policy["Name"] != "on-failure" || policy["MaximumRetryCount"] != RestartAttempts {
		t.Errorf("RestartPolicy = %v", policy)
	}
}

func TestHostLimitsUnlimited(t *testing.T) {
	limits := hostLimits(ContainerSpec{})
	for _, key := range []string{"Memory", "MemorySwap", "NanoCpus"} {
		if _, ok := limits[key]; ok {
			t.Errorf("%s set without a limit", key)
		}
	}
}

func TestListedState(t *testing.T) {
	if s := listedState("running", "Up 3 seconds (health: starting)"); s.Health != "starting" || s.ExitCode != nil {
		t.Errorf("running: %+v", s)
	}
	if s := listedState("exited", "Exited (137) 2 minutes ago"); s.ExitCode == nil || *s.ExitCode != 137 {
		t.Errorf("exited: %+v", s)
	}
}

func TestValidate(t *testing.T) {
	good := ContainerSpec{Image: "alpine", DataPath: "/data", Mounts: []Mount{{Subpath: "server/config", Path: "/config"}}}
	if err := good.Validate(); err != nil {
		t.Fatalf("valid spec rejected: %v", err)
	}
	bad := map[string]ContainerSpec{
		"relative data path": {Image: "alpine", DataPath: "data"},
		"root data path":     {Image: "alpine", DataPath: "/"},
		"bind options":       {Image: "alpine", DataPath: "/data:/host"},
		"subpath escape":     {Image: "alpine", DataPath: "/data", Mounts: []Mount{{Subpath: "../other", Path: "/x"}}},
		"unclean subpath":    {Image: "alpine", DataPath: "/data", Mounts: []Mount{{Subpath: "a/../../b", Path: "/x"}}},
		"absolute subpath":   {Image: "alpine", DataPath: "/data", Mounts: []Mount{{Subpath: "/etc", Path: "/x"}}},
		"no image":           {DataPath: "/data"},
	}
	for name, spec := range bad {
		if spec.Validate() == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
