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
 *
 * Android's asset packaging mangles files ending in .gz, so mobile/build.sh stores them with an
 * extra ".lpz" (the game itself, lifepi.tar.gz, among them); they're served under their real names.
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
        String path = url.getPath();
        if (path != null) path = path.replaceAll("/+", "/");     // pygbag asks for ".../0.9.3//x.js" too
        if (!"GET".equals(request.getMethod()) || host == null || path == null || path.contains("..")) {
            return super.shouldInterceptRequest(view, request);
        }
        if ("localhost".equals(host) && path.endsWith(".gz")) {
            InputStream in = open("public" + path);
            if (in != null) {
                return new WebResourceResponse("application/gzip", null, 200, "OK", new HashMap<>(), in);
            }
        }
        if (bundled(host)) {
            InputStream in = open("cdn/" + host + path);
            if (in != null) {
                Map<String, String> headers = new HashMap<>();
                // fetched from the app's own origin (https://localhost), maybe with credentials:
                // name the origin rather than "*", which credentialed requests refuse
                String origin = request.getRequestHeaders().get("Origin");
                headers.put("Access-Control-Allow-Origin", origin != null ? origin : "*");
                if (origin != null) headers.put("Access-Control-Allow-Credentials", "true");
                headers.put("Vary", "Origin");
                headers.put("Cache-Control", "max-age=31536000");
                return new WebResourceResponse(mime(path), null, 200, "OK", headers, in);
            }
        }
        return super.shouldInterceptRequest(view, request);       // the app's page, or the network
    }

    /** An asset, also under its ".lpz" name (see above); null if it isn't in the app. */
    private InputStream open(String name) {
        for (String n : new String[]{name, name + ".lpz"}) {
            try {
                return assets.open(n);
            } catch (IOException missing) {
                // try the next name
            }
        }
        return null;
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
