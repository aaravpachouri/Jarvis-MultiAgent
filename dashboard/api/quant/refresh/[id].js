const { proxyBackendGet } = require("../../../lib/backend-proxy");

module.exports = function handler(req, res) {
  return proxyBackendGet(req, res, request => {
    const id = request.query && request.query.id;
    if (typeof id !== "string" || !id || id.length > 100 || id.includes("/") || id.includes("\\")) {
      return null;
    }
    return "/api/quant/refresh/" + encodeURIComponent(id);
  });
};
