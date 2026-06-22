/**
 * Hermes Omniroute Dashboard Plugin — Configuration Area
 *
 * Dedicated configuration page for the Omniroute plugin.
 * Groups all plugin-related settings (token, base URL, image model,
 * TTS model, search provider) in one cohesive UI.
 *
 * Plain IIFE — no build step. Uses window.__HERMES_PLUGIN_SDK__ for
 * React + shadcn primitives.
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
  var useRef = SDK.hooks.useRef;

  var Card = SDK.components.Card;
  var CardContent = SDK.components.CardContent;
  var CardHeader = SDK.components.CardHeader;
  var CardTitle = SDK.components.CardTitle;
  var Badge = SDK.components.Badge;
  var Button = SDK.components.Button;
  var Input = SDK.components.Input;
  var Label = SDK.components.Label;
  var Separator = SDK.components.Separator;

  // Resolve design-system Select or fall back native <select>.
  var Select = SDK.components.Select || function FallbackSelect(props) {
    return h("select", Object.assign({}, props, {
      className: "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm " + (props.className || ""),
    }), props.children);
  };
  var SelectOption = SDK.components.SelectOption || function FallbackOption(props) {
    return h("option", props, props.children);
  };

  var fetchJSON = SDK.fetchJSON;

  // ---------------------------------------------------------------------------
  // Config field definitions — mirrors the audit from the parent task.
  // ---------------------------------------------------------------------------
  var FIELDS = [
    { key: "token", label: "API Token", type: "password", envVar: "OMNIROUTE_TOKEN",
      description: "Omniroute API token. Set via env var or config.yaml (image_gen.omniroute.token).",
      placeholder: "sk-..." },
    { key: "base_url", label: "Base URL", type: "url", envVar: "OMNIROUTE_BASE_URL",
      description: "Omniroute router base URL.",
      placeholder: "https://omniroute.josevictor.me" },
    { key: "image_model", label: "Image Model", type: "text", envVar: "OMNIROUTE_IMAGE_MODEL",
      description: "Default image generation model. Leave empty for auto-detect.",
      placeholder: "flux-1.1-pro" },
    { key: "tts_model", label: "TTS Model", type: "text", envVar: "OMNIROUTE_TTS_MODEL",
      description: "Default text-to-speech model.",
      placeholder: "tts-1" },
    { key: "search_provider", label: "Search Provider", type: "text", envVar: "OMNIROUTE_SEARCH_PROVIDER",
      description: "Pinned search provider (e.g. tavily-search). Leave empty for Omniroute auto-select.",
      placeholder: "tavily-search" },
  ];
 var MODEL_PROVIDER_FIELD = {
 key: "model_provider_model", label: "Default Model", type: "select", envVar: "OMNIROUTE_MODEL",
 description: "Select the model OmniRoute uses for chat completions. Fetch available models from the API.",
 placeholder: "Select a model...",
 };

  // ---------------------------------------------------------------------------
  // OmnirouteConfigPage — main component
  // ---------------------------------------------------------------------------
  function OmnirouteConfigPage() {
    var _config = useState({});
    var config = _config[0], setConfig = _config[1];

    var _envOverride = useState({});
    var envOverride = _envOverride[0], setEnvOverride = _envOverride[1];

    var _defaults = useState({});
    var defaults = _defaults[0];

    var _loading = useState(true);
    var loading = _loading[0], setLoading = _loading[1];

    var _saving = useState(false);
    var saving = _saving[0], setSaving = _saving[1];

    var _toast = useState({ message: "", type: "" });
    var toast = _toast[0], setToast = _toast[1];

    var _dirty = useState(false);
    var dirty = _dirty[0], setDirty = _dirty[1];

    var _showToken = useState(false);
    var showToken = _showToken[0], setShowToken = _showToken[1];

 //Model provider state
 var _models = useState([]);
 var models = _models[0], setModels = _models[1];
 var _modelsLoading = useState(false);
 var modelsLoading = _modelsLoading[0], setModelsLoading = _modelsLoading[1];
 var _modelsError = useState("");
 var modelsError = _modelsError[0], setModelsError = _modelsError[1];
    // Track whether any env var overrides are active.
    var hasEnvOverride = Object.values(envOverride).some(function (v) { return v; });

    function showToast(message, type) {
      setToast({ message: message, type: type });
      setTimeout(function () { setToast({ message: "", type: "" }); }, 3000);
    }


 function fetchModels(){
 setModelsLoading(true);
 setModelsError("");
 fetchJSON("/api/plugins/omniroute/models")
 .then(function(data){
 setModels(data.models || []);
 setModelsError(data.error || "");
 })
 .catch(function(){
 setModels([]);
 setModelsError("Failed to fetch models.");
 })
 .finally(function(){
 setModelsLoading(false);
 });
 }
    // Load config on mount.
    useEffect(function () {
      setLoading(true);
      fetchJSON("/api/plugins/omniroute/config")
        .then(function (data) {
          setConfig(data.config || {});
          setEnvOverride(data.env_override || {});
          setDefaults(data.defaults || {});
          setDirty(false);
        })
        .catch(function () {
          showToast("Failed to load configuration", "error");
        })
        .finally(function () {
          setLoading(false);
        });
    }, []);


 //Fetch models when config is loaded (if we have a token).
 useEffect(function(){
 if(!loading && config.token){
 fetchModels();
 }
 }, [loading]);
    function handleChange(key, value) {
      var next = Object.assign({}, config);
      next[key] = value;
      setConfig(next);
      setDirty(true);
    }

    function handleSave() {
      setSaving(true);
      fetchJSON("/api/plugins/omniroute/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      })
        .then(function (data) {
          if (data.success) {
            showToast("Configuration saved!", "success");
            setDirty(false);
          } else {
            showToast(data.message || "Save failed", "error");
          }
        })
        .catch(function () {
          showToast("Save failed — network error", "error");
        })
        .finally(function () {
          setSaving(false);
        });
    }

    function handleReset(key) {
      var next = Object.assign({}, config);
      next[key] = defaults[key] || "";
      setConfig(next);
      setDirty(true);
    }

    // ---- Render helpers ----

    function renderField(field) {
      var value = config[field.key] || "";
      var envActive = envOverride[field.key];
      var isPassword = field.type === "password";
      var inputType = isPassword && !showToken ? "password" : "text";

      return h("div", { key: field.key, className: "omniroute-field" },
        h("div", { className: "omniroute-field-header" },
          h(Label, { className: "omniroute-label" }, field.label),
          h("span", { className: "omniroute-env-var" },
            field.envVar,
            envActive ? h(Badge, { variant: "warning", className: "omniroute-badge-env" }, "ENV override") : null
          )
        ),
        h("div", { className: "omniroute-field-input-row" },
          h(Input, {
            type: inputType,
            value: value,
            placeholder: field.placeholder,
            className: "omniroute-input",
            onChange: function (e) { handleChange(field.key, e.target.value); },
          }),
          isPassword ? h(Button, {
            variant: "ghost", size: "sm",
            onClick: function () { setShowToken(!showToken); },
          }, showToken ? "Hide" : "Show") : null
        ),
        h("p", { className: "omniroute-field-desc" }, field.description)
      );
    }


 function renderModelProvider(){
 var currentValue = config[MODEL_PROVIDER_FIELD.key] || "";
 var envActive = envOverride[MODEL_PROVIDER_FIELD.key];
 var hasModels = models.length > 0;

 return h("div", { className: "omniroute-model-provider" },
 // Model dropdown
 h("div", { className: "omniroute-field-header" },
 h(Label, { className: "omniroute-label" }, MODEL_PROVIDER_FIELD.label),
 h("span", { className: "omniroute-env-var" },
 MODEL_PROVIDER_FIELD.envVar,
 envActive ? h(Badge, { variant: "warning", className: "omniroute-badge-env" }, "ENV override") : null
 )
 ),
 h("div", { className: "omniroute-model-select-row" },
 h(Select, {
 value: currentValue,
 className: "omniroute-input",
 onChange: function(e){ handleChange(MODEL_PROVIDER_FIELD.key, e.target.value); },
 },
 h("option", { value: "" }, MODEL_PROVIDER_FIELD.placeholder),
 hasModels ? models.map(function(m){
 return h(SelectOption, { key: m.id, value: m.id }, m.name || m.id);
 }) : null
 ),
 h(Button, {
 variant: "ghost", size: "sm",
 onClick: fetchModels,
 disabled: modelsLoading,
 className: "omniroute-refresh-btn",
 }, modelsLoading ? "Loading..." : "Refresh")
 ),
 // Error message
 modelsError ? h("p", { className: "omniroute-model-error" }, modelsError) : null,
 // Description
 h("p", { className: "omniroute-field-desc" }, MODEL_PROVIDER_FIELD.description)
 );
 }
    if (loading) {
      return h("div", { className: "omniroute-loading" }, "Loading configuration...");
    }

    return h("div", { className: "omniroute-config-page" },
      // Page header
      h("div", { className: "omniroute-page-header" },
        h("h1", null, "Omniroute Configuration"),
        h("p", { className: "omniroute-subtitle" },
          "Manage all Omniroute plugin settings from one place."
        )
      ),

      // Env override warning
      hasEnvOverride ? h("div", { className: "omniroute-env-warning" },
        h(Badge, { variant: "warning" }, "Environment variables active"),
        h("span", null, "Some config values are overridden by environment variables. ",
          "To edit them here, remove the env var from your shell or config.")
      ) : null,

      // Config form
      h(Card, { className: "omniroute-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Connection Settings")
        ),
        h(CardContent, null,
          renderField(FIELDS[0]),  // token
          renderField(FIELDS[1]),  // base_url
        )
      ),

      h(Card, { className: "omniroute-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Model Preferences")
        ),
        h(CardContent, null,
          renderField(FIELDS[2]),  // image_model
          renderField(FIELDS[3]),  // tts_model
        )
      ),


 // Model Provider
 h(Card, { className: "omniroute-card" },
 h(CardHeader, null,
 h(CardTitle, null, "Model Provider")
 ),
 h(CardContent, null,
 renderModelProvider()
 )
 ),
      h(Card, { className: "omniroute-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Web Search")
        ),
        h(CardContent, null,
          renderField(FIELDS[4]),  // search_provider
        )
      ),

      // Save bar
      h("div", { className: "omniroute-save-bar" },
        h(Button, {
          onClick: handleSave,
          disabled: !dirty || saving,
          className: "omniroute-save-btn",
        }, saving ? "Saving..." : "Save Configuration"),
        dirty ? h("span", { className: "omniroute-unsaved" }, "Unsaved changes") : null
      ),

      // Toast
      toast.message ? h("div", {
        className: "omniroute-toast omniroute-toast-" + toast.type,
      }, toast.message) : null
    );
  }

  // Register the plugin tab component.
  window.__HERMES_PLUGINS__.register("omniroute", OmnirouteConfigPage);
})();
