import type { Checkout, RazorpayResult } from "./api";

interface RazorpayOptions {
  key: string;
  order_id: string;
  amount: number;
  currency: string;
  name: string;
  prefill: { email: string; contact: string };
  handler: (r: { razorpay_payment_id: string; razorpay_order_id: string; razorpay_signature: string }) => void;
  modal: { ondismiss: () => void };
}
declare global {
  interface Window {
    Razorpay?: new (o: RazorpayOptions) => { open(): void; on(ev: string, cb: (r: { error?: { description?: string } }) => void): void };
  }
}

let loading: Promise<void> | null = null;
function loadScript(): Promise<void> {
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => resolve();
    s.onerror = () => {
      loading = null;
      reject(new Error("Couldn't load the payment window. Check your connection and try again."));
    };
    document.head.appendChild(s);
  });
  return loading;
}

/** Opens Razorpay Checkout. Resolves with the result to send to /confirm, or null if the user closed it. */
export async function payWithRazorpay(co: Checkout, prefill: { email: string; contact: string }): Promise<RazorpayResult | null> {
  await loadScript();
  if (!window.Razorpay || !co.gateway_key_id || !co.gateway_order_id) throw new Error("Payments are not available right now.");
  return new Promise((resolve, reject) => {
    const rzp = new window.Razorpay!({
      key: co.gateway_key_id!,
      order_id: co.gateway_order_id!,
      amount: co.amount,
      currency: co.currency,
      name: "Universal Travel Ecosystem",
      prefill,
      handler: (r) => resolve({ payment_id: r.razorpay_payment_id, signature: r.razorpay_signature }),
      modal: { ondismiss: () => resolve(null) },
    });
    rzp.on("payment.failed", (r) => reject(new Error(r.error?.description ?? "Payment failed. You have not been charged.")));
    rzp.open();
  });
}
