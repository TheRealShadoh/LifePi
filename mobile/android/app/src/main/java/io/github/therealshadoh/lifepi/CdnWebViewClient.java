package io.github.therealshadoh.lifepi;

import android.content.res.AssetManager;
import android.net.Uri;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;

import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeWebViewClient;

import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/**
 * The web build loads pygbag's Python runtime (CPython in WebAssembly, pygame) from its CDN.
 * The build copies those files into the app under assets/cdn/<host>/<path>
 * (mobile/vendor_cdn.py); this answers the page's requests for them from there, so LifePi starts
 * offline - at a game store with no Wi-Fi, say. Anything not bundled goes to the network as usual.
 */
public class CdnWebViewClient extends BridgeWebViewClient {
    private static final String[] HOSTS = {"pygame-web.github.io"};
    private final AssetManager assets;

    public CdnWebViewClient(Bridge bridge, AssetManager assets) {
        super(bridge);
        this.assets = assets;
    }

    @Override
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
        Uri url = request.getUrl();
        String host = url.getHost();
        if ("GET".equals(request.getMethod()) && host != null && bundled(host)) {
            String path = url.getPath();
            if (path != null && !path.contains("..")) {
                try {
                    InputStream in = assets.open("cdn/" + host + path);
                    Map<String, String> headers = new HashMap<>();
                    headers.put("Access-Control-Allow-Origin", "*");   // fetched from https://localhost
                    headers.put("Cache-Control", "max-age=31536000");
                    return new WebResourceResponse(mime(path), null, 200, "OK", headers, in);
                } catch (IOException notBundled) {
                    // fall through to the network
                }
            }
        }
        return super.shouldInterceptRequest(view, request);
    }

    private static boolean bundled(String host) {
        for (String h : HOSTS) {
            if (h.equals(host)) return true;
        }
        return false;
    }

    static String mime(String path) {
        String p = path.toLowerCase();
        if (p.endsWith(".wasm")) return "application/wasm";        // streaming compile needs this
        if (p.endsWith(".js") || p.endsWith(".mjs")) return "text/javascript";
        if (p.endsWith(".json") || p.endsWith(".map")) return "application/json";
        if (p.endsWith(".html")) return "text/html";
        if (p.endsWith(".css")) return "text/css";
        if (p.endsWith(".png")) return "image/png";
        if (p.endsWith(".svg")) return "image/svg+xml";
        if (p.endsWith(".ttf")) return "font/ttf";
        if (p.endsWith(".woff2")) return "font/woff2";
        if (p.endsWith(".txt") || p.endsWith(".py")) return "text/plain";
        return "application/octet-stream";                          // .whl, .tar, .apk (pygbag)...
    }
}
