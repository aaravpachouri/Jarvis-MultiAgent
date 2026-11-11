const { proxyBackendGet } = require("../../lib/backend-proxy");

module.exports = function handler(req, res) {
  return proxyBackendGet(req, res, request => {
    const name = request.query && request.query.name;
    if (typeof name !== "string" || !name || name.length > 180 || name.includes("/") || name.includes("\\")) {
      return null;
    }
    return "/api/report/" + encodeURIComponent(name);
  });
};
