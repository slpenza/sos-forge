// CloudFront viewer-request function (JS 2.0 runtime). A folder URL serves its index.html, and /help without
// its slash redirects to /help/ so the help viewer's relative links resolve. Tested by site-stack.test.mjs.
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri === '/help') {
    return { statusCode: 301, statusDescription: 'Moved Permanently', headers: { location: { value: '/help/' } } };
  }
  if (uri.endsWith('/')) {
    request.uri = uri + 'index.html';
  }
  return request;
}
