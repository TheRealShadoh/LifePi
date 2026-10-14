// LifePi Android: the bridge between the game (lifepi.py, running in this page) and the app's
// ads and purchase. Added to the page by mobile/build.sh; not part of the web / phone build.
//
// The game calls (through pygbag's `platform.window.lifepiNative`):
//   want(on)  - a menu / history / dice sheet is open (true) or closed (false). The banner is
//               shown only while one is; the sheet leaves the strip at the bottom free for it.
//   sheets(n) - how many sheets are open (the Back button closes the top one)
//   buy()     - the Remove ads purchase        privacy() - Google's ad privacy form
// and the app calls back() for Android's Back button.
// and reads: ads (show the Remove ads button), removed, reserve (CSS px to leave free at the
// bottom right now: the banner's height while it's up, the last height while it loads),
// price, privacyRequired, notice (one-off message for a toast; the game clears it).
(function () {
  const CFG = window.LIFEPI_ADS || {};
  const TEST_BANNER = "ca-app-pub-3940256099942544/9214589741";     // Google's sample adaptive banner
  const bannerId = CFG.bannerId || TEST_BANNER;
  const testing = !CFG.bannerId;
  const KEY = "lifepi-adfree";
  const store = {
    get() { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; } },
    set(v) { try { localStorage.setItem(KEY, v ? "1" : "0"); } catch (e) {} },
  };

  const N = window.lifepiNative = {
    ads: false, removed: store.get(), height: 0, price: "", privacyRequired: false, notice: "",
    get reserve() { return N._shown && !N.removed ? (N.height || N._last) : 0; },
    _last: (() => { try { return +localStorage.getItem("lifepi-banner-h") || 0; } catch (e) { return 0; } })(),
    _want: false, _shown: false, _loaded: false, _starting: null, _sheets: 0,
    _calls: 0,                                          // how often the game has called in (tests)
    want(on) { N._calls++; on = !!on; if (on !== N._want) { N._want = on; sync(); } },
    sheets(n) { N._calls++; N._sheets = n | 0; },
    buy() { buy(); },
    privacy() { plugin("AdMob") && plugin("AdMob").showPrivacyOptionsForm().catch(() => {}); },
  };

  function plugin(name) {
    const C = window.Capacitor;
    return C && C.isNativePlatform && C.isNativePlatform() && C.Plugins ? C.Plugins[name] : null;
  }

  // Android Back (MainActivity asks): close the open sheet and say so; false = nothing open, so
  // the app goes to the background
  N._backs = 0;
  N.back = function () {
    N._backs++;
    if (N._sheets <= 0) return false;
    const c = document.getElementById("canvas") || window;
    const ev = { key: "Escape", code: "Escape", keyCode: 27, which: 27, bubbles: true };
    c.dispatchEvent(new KeyboardEvent("keydown", ev));
    setTimeout(() => c.dispatchEvent(new KeyboardEvent("keyup", ev)), 40);
    return true;
  };

  const Billing = plugin("LifePiBilling");
  const AdMob = plugin("AdMob");
  if (!AdMob) return;                                   // not the Android app: nothing to do

  // Bought before (this device or another)? Play remembers; the local copy covers offline starts.
  if (Billing) {
    Billing.owned().then((r) => { if (r.owned) removeAds(); else { store.set(false); N.removed = false; price(); } })
      .catch(() => {});
    Billing.addListener("purchase", (r) => { if (r.owned) removeAds("Thanks for supporting LifePi! Ads are gone."); });
  }
  N.ads = !N.removed;

  function price() {
    Billing && Billing.product().then((r) => { N.price = r.price || ""; }).catch(() => {});
  }

  function removeAds(msg) {
    N.removed = true; N.ads = false; store.set(true);
    if (msg) N.notice = msg;
    if (N._shown) { AdMob.removeBanner().catch(() => {}); N._shown = false; }
    N.height = 0;
  }

  function buy() {
    if (!Billing) { N.notice = "Purchases need the Google Play version of LifePi"; return; }
    Billing.buy().then((r) => {
      if (r.owned) removeAds("Thanks for supporting LifePi! Ads are gone.");
      else if (r.pending) N.notice = "Payment pending - ads go once it clears";
      else if (!r.cancelled) N.notice = "Purchase didn't go through" + (r.error ? ": " + r.error : "");
    }).catch((e) => { N.notice = String((e && e.message) || e); });
  }

  // Ads start the first time a sheet opens - never at launch. In the EU / UK that's also when
  // Google's consent form appears (once); elsewhere there's no form.
  function start() {
    if (N._starting) return N._starting;
    N._starting = (async () => {
      await AdMob.initialize({ initializeForTesting: testing });
      let info = await AdMob.requestConsentInfo();
      if (info.isConsentFormAvailable && info.status === "REQUIRED") info = await AdMob.showConsentForm();
      N.privacyRequired = info.privacyOptionsRequirementStatus === "REQUIRED";
      AdMob.addListener("bannerAdSizeChanged", (s) => {
        const h = Math.ceil(s.height || 0);
        if (h > 0) { N.height = N._last = h; try { localStorage.setItem("lifepi-banner-h", String(h)); } catch (e) {} }
      });
      AdMob.addListener("bannerAdLoaded", () => { N._loaded = true; });
      // no ad to show (offline, no fill): give the space back, try again next time
      AdMob.addListener("bannerAdFailedToLoad", () => { N._loaded = false; N.height = N._last = 0; });
      return info.canRequestAds;
    })().catch(() => false);
    return N._starting;
  }

  async function sync() {
    if (N.removed) return;
    if (!N._want) {
      if (N._shown) { N._shown = false; AdMob.hideBanner().catch(() => {}); }
      return;
    }
    const ok = await start();
    if (!ok || !N._want || N.removed) return;
    if (!N._shown) {
      N._shown = true;
      if (N._loaded) AdMob.resumeBanner().catch(() => {});
      else AdMob.showBanner({ adId: bannerId, adSize: "ADAPTIVE_BANNER", position: "BOTTOM_CENTER",
                              margin: 0, isTesting: testing, immersiveMode: true }).catch(() => { N._shown = false; });
    }
  }
})();
