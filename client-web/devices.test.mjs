import test from "node:test";
import assert from "node:assert/strict";
import {
  audioConstraints,
  chooseDevice,
  deviceLabel,
  deviceOptions,
  devicesLocked,
  isMissingDevice,
  loadChoice,
  micLine,
  saveChoice,
  sinkSupported,
  videoConstraints,
} from "./devices.js";

const granted = [
  { kind: "audioinput", deviceId: "default", label: "Default - MacBook Pro Microphone (Built-in)" },
  { kind: "audioinput", deviceId: "mac", label: "MacBook Pro Microphone (Built-in)" },
  { kind: "audioinput", deviceId: "pods", label: "AirPods Pro" },
  { kind: "videoinput", deviceId: "face", label: "FaceTime HD Camera" },
  { kind: "audiooutput", deviceId: "default", label: "Default - MacBook Pro Speakers" },
];
const locked = [
  { kind: "audioinput", deviceId: "", label: "" },
  { kind: "videoinput", deviceId: "", label: "" },
];

test("lists fold Chrome's default alias into one Default entry", () => {
  assert.deepEqual(deviceOptions(granted, "audioinput"), [
    { id: "", label: "Default (MacBook Pro Microphone (Built-in))" },
    { id: "mac", label: "MacBook Pro Microphone (Built-in)" },
    { id: "pods", label: "AirPods Pro" },
  ]);
  assert.deepEqual(deviceOptions(locked, "audioinput"), [{ id: "", label: "Default" }]);
  assert.equal(deviceLabel(granted, "audioinput", "pods"), "AirPods Pro");
  assert.equal(deviceLabel(granted, "audioinput", "nope"), "Default (MacBook Pro Microphone (Built-in))");
});

test("locked lists are detected before permission", () => {
  assert.equal(devicesLocked(locked, "audioinput"), true);
  assert.equal(devicesLocked(granted, "audioinput"), false);
  assert.equal(devicesLocked([], "audioinput"), false);
});

test("choose keeps a present device and falls back when it is unplugged", () => {
  assert.deepEqual(chooseDevice(granted, "audioinput", "pods"), { id: "pods", gone: false });
  const unplugged = granted.filter((d) => d.deviceId !== "pods");
  assert.deepEqual(chooseDevice(unplugged, "audioinput", "pods"), { id: "", gone: true });
  assert.deepEqual(chooseDevice(granted, "audioinput", ""), { id: "", gone: false });
  // Before permission ids are hidden: never treat that as unplugged.
  assert.deepEqual(chooseDevice(locked, "audioinput", "pods"), { id: "pods", gone: false });
});

test("constraints keep the noise settings and pin the device", () => {
  const base = { echoCancellation: true, noiseSuppression: true };
  assert.deepEqual(audioConstraints(base, "pods"), { audio: { echoCancellation: true, noiseSuppression: true, deviceId: { exact: "pods" } } });
  assert.deepEqual(audioConstraints(base, ""), { audio: base });
  assert.equal(videoConstraints("").facingMode, "environment");
  assert.deepEqual(videoConstraints("face").deviceId, { exact: "face" });
  assert.equal(videoConstraints("face").facingMode, undefined);
});

test("missing device errors and the mic line", () => {
  assert.equal(isMissingDevice({ name: "OverconstrainedError" }), true);
  assert.equal(isMissingDevice({ name: "NotFoundError" }), true);
  assert.equal(isMissingDevice({ name: "NotAllowedError" }), false);
  assert.equal(micLine("MacBook Pro Microphone", true), "Mic: MacBook Pro Microphone");
  assert.equal(micLine("", false), "Mic off · Default");
});

test("choice storage survives a throwing localStorage", () => {
  const map = new Map();
  const store = { getItem: (k) => map.get(k) ?? null, setItem: (k, v) => map.set(k, v), removeItem: (k) => map.delete(k) };
  saveChoice(store, "audioinput", "pods");
  assert.equal(loadChoice(store, "audioinput"), "pods");
  saveChoice(store, "audioinput", "");
  assert.equal(loadChoice(store, "audioinput"), "");
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(loadChoice(broken, "videoinput"), "");
  assert.doesNotThrow(() => saveChoice(broken, "videoinput", "face"));
  assert.equal(loadChoice(null, "audioinput"), "");
});

test("speaker row only where setSinkId exists", () => {
  class WithSink { setSinkId() {} }
  class NoSink {}
  assert.equal(sinkSupported({ AudioContext: WithSink }), true);
  assert.equal(sinkSupported({ AudioContext: NoSink }), false);
  assert.equal(sinkSupported({}), false);
});
