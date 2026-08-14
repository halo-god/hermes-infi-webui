import { beforeEach, describe, expect, it } from "vitest";

import { fmtBytes, fmtNum, extractDetail } from "@/utils/format";
import { pickDefaultProfile, rememberProfile, storedProfileId } from "@/utils/profilePref";

describe("format utils", () => {
  it("fmtNum formats with separators", () => {
    expect(fmtNum(1234)).toBe("1,234");
    expect(fmtNum(0)).toBe("0");
    expect(fmtNum(null)).toBe("0");
    expect(fmtNum(undefined)).toBe("0");
  });

  it("fmtBytes human-readable", () => {
    expect(fmtBytes(0)).toBe("0 B");
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(2048)).toBe("2.0 KB");
    expect(fmtBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(fmtBytes(3 * 1024 ** 3)).toBe("3.0 GB");
    expect(fmtBytes(null)).toBe("0 B");
  });

  it("extractDetail handles axios-style detail and strings", () => {
    expect(extractDetail({ response: { data: { detail: "权限不足" } } })).toBe("权限不足");
    expect(extractDetail(new Error("boom"))).toBe("boom");
    expect(extractDetail("直接字符串")).toBe("直接字符串");
    expect(extractDetail(undefined)).toBe("未知错误");
  });
});

describe("profilePref", () => {
  beforeEach(() => localStorage.clear());

  it("rememberProfile stores and storedProfileId reads", () => {
    expect(storedProfileId()).toBeNull();
    rememberProfile("p-123");
    expect(storedProfileId()).toBe("p-123");
    rememberProfile(null);
    expect(storedProfileId()).toBeNull();
  });

  it("pickDefaultProfile: empty list → null", () => {
    expect(pickDefaultProfile([])).toBeNull();
  });

  it("pickDefaultProfile: no memory → first profile", () => {
    const profiles = [{ id: "a" }, { id: "b" }];
    expect(pickDefaultProfile(profiles)?.id).toBe("a");
  });

  it("pickDefaultProfile: remembered profile wins", () => {
    rememberProfile("b");
    const profiles = [{ id: "a" }, { id: "b" }];
    expect(pickDefaultProfile(profiles)?.id).toBe("b");
  });

  it("pickDefaultProfile: stale memory falls back to first", () => {
    rememberProfile("gone");
    const profiles = [{ id: "a" }];
    expect(pickDefaultProfile(profiles)?.id).toBe("a");
  });
});
