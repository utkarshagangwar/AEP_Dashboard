/**
 * Proxy utility for forwarding requests to FastAPI backend
 */

export async function proxyToFastAPI(request, endpoint) {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const url = `${apiUrl}${endpoint}`;

  try {
    const options = {
      method: request.method,
      headers: {
        'Content-Type': 'application/json',
      },
    };

    // Forward authorization header if present
    if (request.headers.get('authorization')) {
      options.headers['Authorization'] = request.headers.get('authorization');
    }

    // Forward request body for non-GET requests
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      options.body = await request.text();
    }

    const response = await fetch(url, options);
    const data = await response.text();

    return new Response(data, {
      status: response.status,
      headers: {
        'Content-Type': response.headers.get('content-type') || 'application/json',
      },
    });
  } catch (error) {
    console.error('Proxy error:', error);
    return new Response(
      JSON.stringify({ error: 'Proxy request failed', details: error.message }),
      {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }
}
