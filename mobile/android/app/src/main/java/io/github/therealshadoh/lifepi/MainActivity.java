package io.github.therealshadoh.lifepi;

import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;

import androidx.activity.OnBackPressedCallback;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import com.getcapacitor.BridgeActivity;

/**
 * LifePi's Android shell: the phone/web build of lifepi.py in a WebView (Capacitor).
 *
 *  - keeps the screen on while the app is open (a life counter sits on the table for hours)
 *  - full screen: the status and navigation bars slide away; swipe from an edge to see them
 *  - serves pygbag's Python runtime from inside the app (see CdnWebViewClient), so it starts
 *    with no internet at all
 *  - the Remove ads purchase (BillingPlugin)
 *  - Back closes LifePi's open sheet (menu, history...); with none open the app goes to the
 *    background, keeping the game as it is
 */
public class MainActivity extends BridgeActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        registerPlugin(BillingPlugin.class);
        super.onCreate(savedInstanceState);
        if (bridge != null) {
            bridge.setWebViewClient(new CdnWebViewClient(bridge, getAssets()));
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        immersive();
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (bridge == null || bridge.getWebView() == null) {
                    moveTaskToBack(true);
                    return;
                }
                bridge.getWebView().evaluateJavascript(
                        "(window.lifepiNative && window.lifepiNative.back) ? window.lifepiNative.back() : false",
                        handled -> {
                            if (!"true".equals(handled)) moveTaskToBack(true);
                        });
            }
        });
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            immersive();               // back from a dialog (purchase, consent): hide the bars again
        }
    }

    private void immersive() {
        View decor = getWindow().getDecorView();
        WindowInsetsControllerCompat c = WindowCompat.getInsetsController(getWindow(), decor);
        c.setSystemBarsBehavior(WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        c.hide(WindowInsetsCompat.Type.systemBars());
    }
}
