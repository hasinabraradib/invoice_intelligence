"use client";
import { useSyncExternalStore } from "react";

const KEY = "invoice-reviewer";
const EVENT = "invoice-reviewer-change";

function read(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // storage blocked (private mode): the name just isn't remembered
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange); // other tabs
  window.addEventListener(EVENT, onChange); // this tab
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

/** The reviewer's name for the audit trail, remembered in this browser only.
 * useSyncExternalStore reads localStorage safely: "" on the server, real value in the browser. */
export function useReviewer(): [string, (name: string) => void] {
  const name = useSyncExternalStore(subscribe, read, () => "");
  const save = (next: string) => {
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // ignore
    }
    window.dispatchEvent(new Event(EVENT));
  };
  return [name, save];
}
