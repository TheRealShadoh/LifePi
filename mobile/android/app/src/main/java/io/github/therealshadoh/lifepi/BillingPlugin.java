package io.github.therealshadoh.lifepi;

import androidx.annotation.NonNull;

import com.android.billingclient.api.AcknowledgePurchaseParams;
import com.android.billingclient.api.BillingClient;
import com.android.billingclient.api.BillingClientStateListener;
import com.android.billingclient.api.BillingFlowParams;
import com.android.billingclient.api.BillingResult;
import com.android.billingclient.api.PendingPurchasesParams;
import com.android.billingclient.api.ProductDetails;
import com.android.billingclient.api.Purchase;
import com.android.billingclient.api.PurchasesUpdatedListener;
import com.android.billingclient.api.QueryProductDetailsParams;
import com.android.billingclient.api.QueryPurchasesParams;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * The one-time "Remove ads" purchase (Google Play Billing). One in-app product, a one-time
 * purchase with the ID "remove_ads", set up in Play Console > Monetize > In-app products.
 *
 * From the page (window.Capacitor.Plugins.LifePiBilling):
 *   owned()   -> {owned: bool, pending: bool}   bought before (on any of the user's devices)?
 *   product() -> {price: "$2.99"}                the price in the user's own currency
 *   buy()     -> {owned: bool, pending: bool, cancelled: bool}
 * Purchases are acknowledged here, or Google refunds them after three days.
 */
@CapacitorPlugin(name = "LifePiBilling")
public class BillingPlugin extends Plugin implements PurchasesUpdatedListener {
    static final String PRODUCT = "remove_ads";
    private BillingClient client;
    private PluginCall buying;
    private final List<Runnable> waiting = new ArrayList<>();
    private boolean connecting;

    @Override
    public void load() {
        client = BillingClient.newBuilder(getContext())
                .setListener(this)
                .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
                .enableAutoServiceReconnection()
                .build();
    }

    /** Run once connected to Play; calls fail with a message if Play isn't there. */
    private synchronized void ready(PluginCall call, Runnable then) {
        if (client.isReady()) {
            then.run();
            return;
        }
        waiting.add(then);
        if (connecting) return;
        connecting = true;
        client.startConnection(new BillingClientStateListener() {
            @Override
            public void onBillingSetupFinished(@NonNull BillingResult r) {
                List<Runnable> run;
                synchronized (BillingPlugin.this) {
                    connecting = false;
                    run = new ArrayList<>(waiting);
                    waiting.clear();
                }
                if (r.getResponseCode() == BillingClient.BillingResponseCode.OK) {
                    for (Runnable x : run) x.run();
                } else if (call != null) {
                    call.reject("Google Play billing unavailable: " + r.getDebugMessage());
                }
            }

            @Override
            public void onBillingServiceDisconnected() {
                synchronized (BillingPlugin.this) {
                    connecting = false;    // auto reconnection handles calls from here on
                }
            }
        });
    }

    @PluginMethod
    public void owned(PluginCall call) {
        ready(call, () -> client.queryPurchasesAsync(
                QueryPurchasesParams.newBuilder().setProductType(BillingClient.ProductType.INAPP).build(),
                (r, purchases) -> {
                    if (r.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                        call.reject("Couldn't check purchases: " + r.getDebugMessage());
                        return;
                    }
                    call.resolve(state(purchases, false));
                }));
    }

    @PluginMethod
    public void product(PluginCall call) {
        ready(call, () -> details(call, d -> {
            JSObject out = new JSObject();
            ProductDetails.OneTimePurchaseOfferDetails o = d.getOneTimePurchaseOfferDetails();
            out.put("price", o != null ? o.getFormattedPrice() : "");
            call.resolve(out);
        }));
    }

    @PluginMethod
    public void buy(PluginCall call) {
        ready(call, () -> details(call, d -> {
            BillingFlowParams params = BillingFlowParams.newBuilder()
                    .setProductDetailsParamsList(Collections.singletonList(
                            BillingFlowParams.ProductDetailsParams.newBuilder().setProductDetails(d).build()))
                    .build();
            buying = call;
            getActivity().runOnUiThread(() -> {
                BillingResult r = client.launchBillingFlow(getActivity(), params);
                if (r.getResponseCode() != BillingClient.BillingResponseCode.OK && buying != null) {
                    buying = null;
                    call.reject("Couldn't start the purchase: " + r.getDebugMessage());
                }
            });
        }));
    }

    @Override
    public void onPurchasesUpdated(@NonNull BillingResult r, List<Purchase> purchases) {
        PluginCall call = buying;
        buying = null;
        int code = r.getResponseCode();
        if (code == BillingClient.BillingResponseCode.OK) {
            JSObject out = state(purchases, true);
            if (call != null) call.resolve(out);
            else notifyListeners("purchase", out);
        } else if (code == BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED) {
            JSObject out = new JSObject();
            out.put("owned", true);
            out.put("pending", false);
            if (call != null) call.resolve(out);
        } else if (call != null) {
            JSObject out = new JSObject();
            out.put("owned", false);
            out.put("pending", false);
            out.put("cancelled", code == BillingClient.BillingResponseCode.USER_CANCELED);
            out.put("error", code == BillingClient.BillingResponseCode.USER_CANCELED ? "" : r.getDebugMessage());
            call.resolve(out);
        }
    }

    /** {owned, pending} for our product; acknowledges a completed purchase if needed. */
    private JSObject state(List<Purchase> purchases, boolean fresh) {
        boolean owned = false, pending = false;
        if (purchases != null) {
            for (Purchase p : purchases) {
                if (!p.getProducts().contains(PRODUCT)) continue;
                if (p.getPurchaseState() == Purchase.PurchaseState.PURCHASED) {
                    owned = true;
                    if (!p.isAcknowledged()) {
                        client.acknowledgePurchase(AcknowledgePurchaseParams.newBuilder()
                                .setPurchaseToken(p.getPurchaseToken()).build(), ack -> { });
                    }
                } else if (p.getPurchaseState() == Purchase.PurchaseState.PENDING) {
                    pending = true;
                }
            }
        }
        JSObject out = new JSObject();
        out.put("owned", owned);
        out.put("pending", pending);
        out.put("cancelled", false);
        return out;
    }

    private interface WithDetails {
        void run(ProductDetails d);
    }

    private void details(PluginCall call, WithDetails then) {
        QueryProductDetailsParams params = QueryProductDetailsParams.newBuilder()
                .setProductList(Collections.singletonList(QueryProductDetailsParams.Product.newBuilder()
                        .setProductId(PRODUCT).setProductType(BillingClient.ProductType.INAPP).build()))
                .build();
        client.queryProductDetailsAsync(params, (r, result) -> {
            List<ProductDetails> list = result.getProductDetailsList();
            if (r.getResponseCode() != BillingClient.BillingResponseCode.OK || list.isEmpty()) {
                call.reject("Remove ads isn't available yet: " + r.getDebugMessage());
                return;
            }
            then.run(list.get(0));
        });
    }
}
