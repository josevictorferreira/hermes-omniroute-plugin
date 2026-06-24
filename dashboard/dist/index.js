/**
 * Hermes OmniRoute Dashboard Plugin — Connection + Model Selection
 *
 * Surfaces two groups of settings:
 *   1. Connection (persisted via /settings): OmniRoute API key + base URL.
 *   2. Models (persisted via /config): image-generation model, TTS model and
 *      the default provider (chat) model.  Each model field is a searchable
 *      input backed by a <datalist> whose options are fetched live from
 *      /models?capability=image|tts|chat so users pick instead of typing.
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

  var API = "/api/plugins/omniroute";

  // -------------------------------------------------------------------------
  // Connection fields — persisted via the /settings endpoint (masked + env).
  // -------------------------------------------------------------------------
  var CONNECTION_FIELDS = [
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

  // -------------------------------------------------------------------------
  // Model fields — persisted via the /config endpoint. ``capability`` selects
  // which catalog feeds the dropdown.
  // -------------------------------------------------------------------------
  var MODEL_FIELDS = [
    {
      key: "image_model",
      label: "Image Generation Model",
      capability: "image",
      envVar: "OMNIROUTE_IMAGE_MODEL",
      description: "Model used by the image-generation provider.",
      placeholder: "openai/gpt-image-2",
    },
    {
      key: "tts_model",
      label: "Text-to-Speech Model",
      capability: "tts",
      envVar: "OMNIROUTE_TTS_MODEL",
      description: "Model used by the TTS provider (/audio/speech).",
      placeholder: "openai/tts-1",
    },
    {
      key: "model_provider_model",
      label: "Provider (Chat) Model",
      capability: "chat",
      envVar: "OMNIROUTE_MODEL",
      description: "Default model when OmniRoute is selected as the chat model provider.",
      placeholder: "openai/gpt-4o",
    },
  ];

  function showToast(setToast, message, type) {
    setToast({ message: message, type: type });
    setTimeout(function () {
      setToast(null);
    }, 4000);
  }

  function OmnirouteConfigPage() {
    var [conn, setConn] = useState({ api_key: "", base_url: "" });
    var [connEnv, setConnEnv] = useState({});
    var [loadedApiKey, setLoadedApiKey] = useState("");
    var [models, setModels] = useState({
      image_model: "",
      tts_model: "",
      model_provider_model: "",
    });
    var [modelEnv, setModelEnv] = useState({});
    var [options, setOptions] = useState({ image: [], tts: [], chat: [] });
    var [optErrors, setOptErrors] = useState({});
    var [loading, setLoading] = useState(true);
    var [loadingModels, setLoadingModels] = useState(true);
    var [saving, setSaving] = useState(false);
    var [dirty, setDirty] = useState(false);
    var [toast, setToast] = useState(null);

    var hasEnvOverride =
      Object.values(connEnv).some(Boolean) || Object.values(modelEnv).some(Boolean);

    var handleConnChange = useCallback(function (key, value) {
      setConn(function (prev) {
        var next = Object.assign({}, prev);
        next[key] = value;
        return next;
      });
      setDirty(true);
    }, []);

    var handleModelChange = useCallback(function (key, value) {
      setModels(function (prev) {
        var next = Object.assign({}, prev);
        next[key] = value;
        return next;
      });
      setDirty(true);
    }, []);

    var loadModels = useCallback(function () {
      setLoadingModels(true);
      var caps = ["image", "tts", "chat"];
      Promise.all(
        caps.map(function (cap) {
          return fetchJSON(API + "/models?capability=" + cap)
            .then(function (data) {
              return { cap: cap, models: data.models || [], error: data.error || "" };
            })
            .catch(function () {
              return { cap: cap, models: [], error: "Could not reach OmniRoute." };
            });
        })
      )
        .then(function (results) {
          var opts = { image: [], tts: [], chat: [] };
          var errs = {};
          results.forEach(function (r) {
            opts[r.cap] = r.models;
            if (r.error) errs[r.cap] = r.error;
          });
          setOptions(opts);
          setOptErrors(errs);
        })
        .finally(function () {
          setLoadingModels(false);
        });
    }, []);

    var handleLoad = useCallback(function () {
      setLoading(true);
      Promise.all([
        fetchJSON(API + "/settings"),
        fetchJSON(API + "/config"),
      ])
        .then(function (res) {
          var s = res[0] || {};
          var c = res[1] || {};
          var settings = s.settings || { api_key: "", base_url: "" };
          setConn(settings);
          setLoadedApiKey(settings.api_key || "");
          setConnEnv(s.has_env_override || {});
          var cfg = c.config || {};
          setModels({
            image_model: cfg.image_model || "",
            tts_model: cfg.tts_model || "",
            model_provider_model: cfg.model_provider_model || "",
          });
          setModelEnv(c.env_override || {});
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
      // Only send the API key if the user actually changed it — otherwise we
      // would persist the masked value (sk-…***…key) and corrupt the token.
      var apiKeyChanged = (conn.api_key || "") !== loadedApiKey;
      var settingsBody = {
        api_key: apiKeyChanged ? (conn.api_key || "").trim() : "",
        base_url: (conn.base_url || "").trim(),
      };
      // Connection fields are intentionally empty here so /config preserves
      // them — connection lives in the /settings store.
      var configBody = {
        token: "",
        base_url: "",
        search_provider: "",
        image_model: (models.image_model || "").trim(),
        tts_model: (models.tts_model || "").trim(),
        model_provider_model: (models.model_provider_model || "").trim(),
      };

      fetchJSON(API + "/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsBody),
      })
        .then(function (sRes) {
          if (sRes && sRes.success === false) {
            throw new Error(sRes.message || "Failed to save connection.");
          }
          return fetchJSON(API + "/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(configBody),
          });
        })
        .then(function (cRes) {
          if (cRes && cRes.success === false) {
            throw new Error(cRes.message || "Failed to save models.");
          }
          showToast(setToast, "OmniRoute settings saved.", "success");
          setDirty(false);
          handleLoad();
          loadModels();
        })
        .catch(function (err) {
          showToast(setToast, (err && err.message) || "Save failed.", "error");
        })
        .finally(function () {
          setSaving(false);
        });
    }, [conn, models, loadedApiKey, handleLoad, loadModels]);

    useEffect(function () {
      handleLoad();
      loadModels();
    }, [handleLoad, loadModels]);

    function renderEnvHeader(label, fieldKey, envVar, overridden) {
      return h(
        "div",
        { className: "omniroute-field-header" },
        h(Label, { htmlFor: "omniroute-" + fieldKey }, label),
        h(
          "span",
          { className: "omniroute-env-var" },
          overridden
            ? h(Badge, { variant: "warning", className: "omniroute-badge-env" }, "env")
            : null,
          envVar
        )
      );
    }

    function renderConnField(field) {
      var isOverridden = !!connEnv[field.key];
      return h(
        "div",
        { key: field.key, className: "omniroute-field" },
        renderEnvHeader(field.label, field.key, field.envVar, isOverridden),
        h(Input, {
          id: "omniroute-" + field.key,
          type: field.type,
          value: conn[field.key] || "",
          placeholder: field.placeholder,
          disabled: isOverridden || loading,
          onChange: function (e) {
            handleConnChange(field.key, e.target.value);
          },
          className: "omniroute-input",
        }),
        h("p", { className: "omniroute-field-desc" }, field.description)
      );
    }

    function renderModelField(field) {
      var isOverridden = !!modelEnv[field.key];
      var listId = "omniroute-models-" + field.capability;
      var opts = options[field.capability] || [];
      var err = optErrors[field.capability];
      var count = opts.length;
      var hint = loadingModels
        ? "Loading models…"
        : err
        ? err
        : count
        ? count + " models available — type to filter or pick from the list."
        : "No models found.";
      return h(
        "div",
        { key: field.key, className: "omniroute-field" },
        renderEnvHeader(field.label, field.key, field.envVar, isOverridden),
        h(
          "input",
          {
            id: "omniroute-" + field.key,
            type: "text",
            list: listId,
            value: models[field.key] || "",
            placeholder: field.placeholder,
            disabled: isOverridden || loading,
            autoComplete: "off",
            onChange: function (e) {
              handleModelChange(field.key, e.target.value);
            },
            className: "omniroute-input omniroute-select",
          }
        ),
        h(
          "datalist",
          { id: listId },
          opts.map(function (m) {
            var label = m.name && m.name !== m.id ? m.id + " — " + m.name : m.id;
            return h("option", { key: m.id, value: m.id }, label);
          })
        ),
        h(
          "p",
          {
            className:
              "omniroute-field-desc" + (err ? " omniroute-model-error" : ""),
          },
          field.description + " " + hint
        )
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
          "Configure the OmniRoute connection and select the models used for image generation, text-to-speech, and chat routing."
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
        h(CardContent, null, CONNECTION_FIELDS.map(renderConnField))
      ),

      h(
        Card,
        { className: "omniroute-card" },
        h(
          CardHeader,
          null,
          h(CardTitle, null, "Models"),
          h(
            Button,
            {
              variant: "outline",
              onClick: loadModels,
              disabled: loadingModels,
              className: "omniroute-refresh-btn",
            },
            loadingModels ? "Loading..." : "Reload models"
          )
        ),
        h(
          CardContent,
          null,
          MODEL_FIELDS.map(renderModelField),
          h(
            "div",
            { className: "omniroute-helper-text" },
            "Model lists are fetched live from OmniRoute and require a valid API key. Search provider selection remains in the main Hermes settings (Settings → Web)."
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
