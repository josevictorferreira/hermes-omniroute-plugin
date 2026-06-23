/**
 * Hermes OmniRoute Dashboard Plugin — Limited Settings Surface
 *
 * Exposes only the two values that should be configured from this page:
 * OmniRoute API key and OmniRoute base URL.  Model selection (TTS model,
 * image model, default chat model, search provider) is intentionally
 * omitted here and left to the main Hermes configuration.
 *
 * Plain IIFE build step. Uses window.__HERMES_PLUGIN_SDK__ for
 * React shadcn primitives.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;

  var Card = SDK.components.Card;
  var CardContent = SDK.components.CardContent;
  var CardHeader = SDK.components.CardHeader;
  var CardTitle = SDK.components.CardTitle;
  var Badge = SDK.components.Badge;
  var Button = SDK.components.Button;
  var Input = SDK.components.Input;
  var Label = SDK.components.Label;

  var fetchJSON = SDK.fetchJSON;

  // -------------------------------------------------------------------------
  // Field definitions — ONLY api_key and base_url are surfaced here.
  // -------------------------------------------------------------------------
  var FIELDS = [
    {
      key: "api_key",
      label: "OmniRoute API Key",
      type: "password",
      envVar: "OMNIROUTE_API_KEY",
      description: "API key OmniRoute uses to authenticate requests.",
      placeholder: "sk-...",
    },
    {
      key: "base_url",
      label: "OmniRoute Base URL",
      type: "url",
      envVar: "OMNIROUTE_BASE_URL",
      description: "Root URL of the OmniRoute router endpoint.",
      placeholder: "https://omniroute.example.com/api/v1",
    },
  ];

  function showToast(setToast, message, type) {
    setToast({ message: message, type: type });
    setTimeout(function () {
      setToast(null);
    }, 4000);
  }

  function OmnirouteConfigPage() {
    var [settings, setSettings] = useState({ api_key: "", base_url: "" });
    var [envOverride, setEnvOverride] = useState({});
    var [loading, setLoading] = useState(true);
    var [saving, setSaving] = useState(false);
    var [dirty, setDirty] = useState(false);
    var [toast, setToast] = useState(null);

    var hasEnvOverride = Object.values(envOverride).some(Boolean);

    var handleChange = useCallback(function (key, value) {
      setSettings(function (prev) {
        var next = Object.assign({}, prev);
        next[key] = value;
        return next;
      });
      setDirty(true);
    }, []);

    var handleLoad = useCallback(function () {
      setLoading(true);
      fetchJSON("/api/plugins/omniroute/settings")
        .then(function (data) {
          setSettings(data.settings || { api_key: "", base_url: "" });
          setEnvOverride(data.has_env_override || {});
          setDirty(false);
        })
        .catch(function () {
          showToast(setToast, "Failed to load OmniRoute settings.", "error");
        })
        .finally(function () {
          setLoading(false);
        });
    }, []);

    var handleSave = useCallback(function () {
      setSaving(true);
      fetchJSON("/api/plugins/omniroute/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: (settings.api_key || "").trim(),
          base_url: (settings.base_url || "").trim(),
        }),
      })
        .then(function (data) {
          if (data.success) {
            showToast(setToast, "OmniRoute settings saved.", "success");
            if (data.settings) {
              setSettings(data.settings);
            }
            setDirty(false);
          } else {
            showToast(setToast, data.message || "Save failed.", "error");
          }
        })
        .catch(function () {
          showToast(setToast, "Save failed — network error.", "error");
        })
        .finally(function () {
          setSaving(false);
        });
    }, [settings]);

    useEffect(function () {
      handleLoad();
    }, [handleLoad]);

    function renderField(field) {
      var isOverridden = !!envOverride[field.key];
      return h(
        "div",
        { key: field.key, className: "omniroute-field" },
        h(
          "div",
          { className: "omniroute-field-header" },
          h(Label, { htmlFor: "omniroute-" + field.key }, field.label),
          h(
            "span",
            { className: "omniroute-env-var" },
            isOverridden
              ? h(Badge, { variant: "warning", className: "omniroute-badge-env" }, "env")
              : null,
            field.envVar
          )
        ),
        h(Input, {
          id: "omniroute-" + field.key,
          type: field.type,
          value: settings[field.key] || "",
          placeholder: field.placeholder,
          disabled: isOverridden || loading,
          onChange: function (e) {
            handleChange(field.key, e.target.value);
          },
          className: "omniroute-input",
        }),
        h("p", { className: "omniroute-field-desc" }, field.description)
      );
    }

    if (loading) {
      return h("div", { className: "omniroute-loading" }, "Loading OmniRoute settings...");
    }

    return h(
      "div",
      { className: "omniroute-config-page" },
      h(
        "div",
        { className: "omniroute-page-header" },
        h("h1", null, "OmniRoute"),
        h(
          "p",
          { className: "omniroute-subtitle" },
          "Configure the OmniRoute connection. Model selection is handled in the main Hermes settings."
        )
      ),

      hasEnvOverride
        ? h(
            "div",
            { className: "omniroute-env-warning" },
            h(Badge, { variant: "warning" }, "Environment variables active"),
            h(
              "span",
              null,
              "Some values are being overridden by environment variables. Remove the env var to edit them here."
            )
          )
        : null,

      h(
        Card,
        { className: "omniroute-card" },
        h(CardHeader, null, h(CardTitle, null, "Connection")),
        h(
          CardContent,
          null,
          FIELDS.map(renderField),
          h(
            "div",
            { className: "omniroute-helper-text" },
            "TTS model, image model, default chat model, and search provider are configured in the main Hermes settings (Settings → TTS / Image / Web / Model)."
          )
        )
      ),

      h(
        "div",
        { className: "omniroute-save-bar" },
        h(
          Button,
          {
            onClick: handleSave,
            disabled: !dirty || saving,
            className: "omniroute-save-btn",
          },
          saving ? "Saving..." : "Save"
        ),
        dirty ? h("span", { className: "omniroute-unsaved" }, "Unsaved changes") : null
      ),

      toast && toast.message
        ? h(
            "div",
            {
              className:
                "omniroute-toast omniroute-toast-" + (toast.type || "info"),
            },
            toast.message
          )
        : null
    );
  }

  window.__HERMES_PLUGINS__.register("omniroute", OmnirouteConfigPage);
})();
