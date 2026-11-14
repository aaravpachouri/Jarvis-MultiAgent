const { proxyBackendGet, buildQueryPath } = require("../../../lib/backend-proxy");

module.exports = function handler(req, res) {
  return proxyBackendGet(req, res, request =>
    buildQueryPath(request, "/api/quant/stock-search", {
      q: 120,
      region: 8,
      limit: 2,
    })
  );
};
