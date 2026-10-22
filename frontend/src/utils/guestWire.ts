export type WireInvitee = {
  readonly email: string;
  readonly name: string;
};

export type Guest =
  | { readonly kind: "named-invite"; readonly name: string; readonly email: string }
  | { readonly kind: "email-only"; readonly email: string }
  | { readonly kind: "name-only"; readonly name: string };

function isRecord(value: unknown): value is { readonly email?: unknown; readonly name?: unknown } {
  return typeof value === "object" && value !== null;
}

export function guestToWire(guest: Guest): WireInvitee {
  switch (guest.kind) {
    case "named-invite":
      return { email: guest.email, name: guest.name };
    case "email-only":
      return { email: guest.email, name: "" };
    case "name-only":
      return { email: "", name: guest.name };
    default: {
      const _exhaustive: never = guest;
      return _exhaustive;
    }
  }
}

export function guestsFromWire(
  participants: readonly (string | WireInvitee)[] | null | undefined,
): readonly Guest[] {
  let guests: readonly Guest[] = [];
  for (const token of participants ?? []) {
    if (typeof token === "string") {
      const trimmed = token.trim();
      if (trimmed.length === 0) continue;
      if (trimmed.includes("@")) {
        guests = appendGuest(guests, { kind: "email-only", email: trimmed });
      } else {
        guests = appendGuest(guests, { kind: "name-only", name: trimmed });
      }
      continue;
    }
    if (!isRecord(token)) continue;
    const email = String(token.email ?? "").trim();
    const name = String(token.name ?? "").trim();
    if (email.includes("@") && name.length > 0 && !name.includes("@")) {
      guests = appendGuest(guests, { kind: "named-invite", name, email });
    } else if (email.includes("@")) {
      guests = appendGuest(guests, { kind: "email-only", email });
    } else if (name.length > 0 && !name.includes("@")) {
      guests = appendGuest(guests, { kind: "name-only", name });
    }
  }
  return guests;
}

export function participantsForSubmit(blockForm: boolean, guests: readonly Guest[]): WireInvitee[] {
  if (blockForm) return [];
  return guests.map(guestToWire);
}

function sameGuest(a: Guest, b: Guest): boolean {
  if (a.kind === "name-only" || b.kind === "name-only") {
    return a.kind === "name-only" && b.kind === "name-only" && a.name === b.name;
  }
  return a.email.toLowerCase() === b.email.toLowerCase();
}

function appendGuest(guests: readonly Guest[], guest: Guest): readonly Guest[] {
  if (guests.some((item) => sameGuest(item, guest))) return guests;
  return [...guests, guest];
}
