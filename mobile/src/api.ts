import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";

const BASE: string = (Constants.expoConfig?.extra as { apiUrl: string }).apiUrl;
const TOKEN_KEY = "wanderly_token";

export type Mode = "flight" | "train" | "bus";

export interface Offer {
  offer_id: string;
  provider: string;
  kind: Mode | "hotel";
  price: number; // minor units, total for `pax` travellers
  currency: string;
  pax: number;
  expires_at: string;
  carrier?: string;
  number?: string;
  origin?: string;
  destination?: string;
  departure?: string;
  arrival?: string;
  duration_min?: number;
  stops?: number;
  name?: string;
  city?: string;
  stars?: number;
  rating?: number;
  nights?: number;
  price_per_night?: number;
  amenities?: string[];
}

export interface Booking {
  id: string;
  reference: string;
  kind: string;
  provider: string;
  status: string;
  checkout_id: string;
  failure_reason: string | null;
  total_price: number;
  currency: string;
  offer: Offer;
  created_at: string;
}

export interface Checkout {
  id: string;
  status: string;
  amount: number;
  currency: string;
  gateway: "demo" | "razorpay";
  gateway_order_id: string | null;
  gateway_key_id: string | null;
  offers: Offer[];
}
export interface PaymentResult {
  payment_id: string;
  signature: string;
}

export interface Plan {
  currency: string;
  outbound: Offer;
  return: Offer;
  hotel: Offer;
  travelers: number;
  total: number;
  itinerary: { day: number; idea: string }[];
}

export type Card =
  | { type: "offer"; n: number; offer: Offer; link: string }
  | { type: "trip"; total: number; currency: string; offers: Offer[]; link: string };

export interface Place {
  name: string;
  lat: number;
  lon: number;
  city: string | null;
}

export interface Passenger {
  title: string;
  gender: string;
  given_name: string;
  family_name: string;
  born_on: string;
}
export interface Contact {
  email: string;
  phone: string;
}

/** The server answered with an error (as opposed to the network failing). */
export class ApiError extends Error {
  constructor(message: string, public status: number, public code: string) {
    super(message);
  }
}

let token: string | null = null;

export async function loadToken() {
  token = await SecureStore.getItemAsync(TOKEN_KEY);
  return token;
}

export async function setToken(t: string | null) {
  token = t;
  if (t) await SecureStore.setItemAsync(TOKEN_KEY, t);
  else await SecureStore.deleteItemAsync(TOKEN_KEY);
}

async function call<T>(path: string, init: RequestInit & { headers?: Record<string, string> } = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(typeof body.detail === "string" ? body.detail : "Something went wrong.", res.status, body.code ?? "error");
  }
  return body as T;
}

const qs = (o: Record<string, string | number>) =>
  Object.entries(o)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  async register(email: string, name: string, password: string) {
    await setToken((await call<{ access_token: string }>("/v1/auth/register", post({ email, name, password }))).access_token);
  },
  async login(email: string, password: string) {
    await setToken((await call<{ access_token: string }>("/v1/auth/login", post({ email, password }))).access_token);
  },
  places: (q: string) => call<Place[]>(`/v1/places?${qs({ q })}`),
  transport: (kind: Mode, origin: string, destination: string, date: string, adults: number) =>
    call<{ offers: Offer[] }>(`/v1/search/transport?${qs({ kind, origin, destination, date, adults })}`).then((r) => r.offers),
  hotels: (city: string, check_in: string, check_out: string, guests: number) =>
    call<{ offers: Offer[] }>(`/v1/search/hotels?${qs({ city, check_in, check_out, guests })}`).then((r) => r.offers),
  offer: (id: string) => call<Offer>(`/v1/offers/${id}`),
  checkout: (offer_ids: string[]) => call<Checkout>("/v1/checkouts", post({ offer_ids })),
  confirm: (checkoutId: string, payment: PaymentResult | null, passengers: Passenger[], contact: Contact) =>
    call<{ checkout_id: string; status: string; bookings: Booking[] }>(
      `/v1/checkouts/${checkoutId}/confirm`,
      post({ payment, passengers, contact }),
    ),
  bookings: () => call<Booking[]>("/v1/bookings"),
  cancel: (id: string) => call<Booking>(`/v1/bookings/${id}/cancel`, { method: "POST" }),
  plan: (body: { origin: string; destination: string; start: string; end: string; travelers: number; mode: Mode; budget?: number }) =>
    call<Plan>("/v1/trips/plan", post(body)),
  chat: (message: string) => call<{ reply: string; cards: Card[] }>("/v1/agent/chat", post({ message })),
  resetChat: () => call<void>("/v1/agent/session", { method: "DELETE" }),
};

export const money = (minor: number, cur = "INR") => {
  const f = new Intl.NumberFormat("en-IN", { style: "currency", currency: cur });
  const digits = f.resolvedOptions().maximumFractionDigits ?? 2;
  return f.format(minor / 10 ** digits);
};

export const hm = (min: number) => `${Math.floor(min / 60)}h ${min % 60}m`;
export const clock = (iso: string) => iso.slice(11, 16);
