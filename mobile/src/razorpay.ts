import type { Checkout, PaymentResult } from "./api";

interface Sdk {
  open(options: Record<string, unknown>): Promise<{ razorpay_payment_id: string; razorpay_order_id: string; razorpay_signature: string }>;
}

/**
 * Opens Razorpay's native checkout. The SDK is a native module, so it does not exist in Expo Go:
 * it is loaded lazily and we explain what is needed instead of crashing at import time.
 * Resolves with the result to send to /confirm, or null if the customer closed the sheet.
 */
export async function payWithRazorpay(co: Checkout, prefill: { email: string; contact: string }): Promise<PaymentResult | null> {
  let sdk: Sdk;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    sdk = require("react-native-razorpay").default as Sdk;
    if (!sdk?.open) throw new Error("missing");
  } catch {
    throw new Error("Card payments need the development build of the app (they don't work in Expo Go).");
  }
  if (!co.gateway_key_id || !co.gateway_order_id) throw new Error("Payments are not available right now.");
  try {
    const r = await sdk.open({
      key: co.gateway_key_id,
      order_id: co.gateway_order_id,
      amount: co.amount,
      currency: co.currency,
      name: "Universal Travel Ecosystem",
      prefill,
    });
    return { payment_id: r.razorpay_payment_id, signature: r.razorpay_signature };
  } catch (e) {
    // The SDK rejects with {code, description} both for cancellation and for failure.
    const x = e as { code?: number; description?: string };
    // ASSUMPTION, unverified: code 0 means the customer dismissed the sheet. Check the SDK's error codes on a device.
    if (x.code === 0) return null;
    throw new Error(x.description ?? "Payment failed. You have not been charged.");
  }
}
