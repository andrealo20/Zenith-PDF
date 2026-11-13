#version 440

// The Liquid glass of a floating bar: its rim bends the page behind it as a lens does.
// Every length is in device pixels; `pixel` is this fragment's centre, from the bar's top left corner.

layout(location = 0) in vec2 pixel;
layout(location = 0) out vec4 colour;

layout(std140, binding = 0) uniform Lens {
    vec4 bar;        // x, y: the bar's size; z: its corners' radius; w: 1 when two corners are cut instead
    vec4 pictures;   // x, y: the pictures' top left corner, from the bar's; z, w: 1 / the size they cover
    vec4 rim;        // x: the rim's width; y: the glass's thickness; z: the furthest bend; w: the colour fringe
    vec4 shape;      // x: 1 for the circle profile; y: the share of the rim where it starts giving way to the
                     // middle; z: the steepest slope; w: which way is up in clip space (the vertex shader's)
    vec4 tint;       // r, g, b: the tint; a: how much of it lies over the rim
    vec4 highlight;  // the light on the rim and its strength
    vec4 border;     // the fine edge and its strength
    vec4 light;      // x: the edge's width; y: how far in the light fades; z: the shadow's strength;
                     // w: the saturation of what the rim shows (1 leaves it)
    vec4 order;      // x, y: 1 when the sharp, the middle picture is stored blue first; z: 1 to write blue first
};
layout(binding = 1) uniform sampler2D sharp;
layout(binding = 2) uniform sampler2D middle;

const vec2 TO_LIGHT = vec2(-0.52910, -0.84856);  // up and to the left: normalize(-0.53, -0.85)
const float INDEX = 1.5;

// The signed distance from the bar's outline, negative inside.
float outline(vec2 p)
{
    vec2 half_size = 0.5 * bar.xy;
    vec2 c = p - half_size;
    vec2 q = abs(c) - half_size;
    float corner = min(bar.z, min(half_size.x, half_size.y));
    if (bar.w > 0.5) {
        // The top left and bottom right corners cut by `corner`: both are |x + y| <= reach.
        float reach = half_size.x + half_size.y - corner;
        return max(max(q.x, q.y), (abs(c.x + c.y) - reach) * 0.70710678);
    }
    vec2 e = q + corner;
    return length(max(e, 0.0)) + min(max(e.x, e.y), 0.0) - corner;
}

void main()
{
    float inward = max(-outline(pixel), 0.0);
    // The outward normal: the distance's gradient, half a pixel either way.
    vec2 slant = vec2(outline(pixel + vec2(0.5, 0.0)) - outline(pixel - vec2(0.5, 0.0)),
                      outline(pixel + vec2(0.0, 0.5)) - outline(pixel - vec2(0.0, 0.5)));
    float steep = length(slant);
    vec2 normal = steep > 1e-4 ? slant / steep : vec2(0.0);

    // How steeply the glass rises over the rim: 0 on the flat middle.
    float x = clamp(inward / rim.x, 0.0, 1.0);
    float u = 1.0 - x;
    float slope = 0.0;
    if (x < 1.0) {
        slope = shape.x > 0.5 ? u / sqrt(max(1.0 - u * u, 1e-6))
                              : u * u * u * pow(max(1.0 - u * u * u * u, 1e-6), -0.75);
        slope = min(slope, shape.z);
    }

    // A ray straight down, bent by the surface and followed through the glass.
    vec3 ray = refract(vec3(0.0, 0.0, -1.0), normalize(vec3(normal * slope, 1.0)), 1.0 / INDEX);
    vec2 bend = ray.xy / max(-ray.z, 1e-3) * rim.y;
    float moved = length(bend);
    if (moved > rim.z)
        bend *= rim.z / moved;

    // Red, green and blue part with the bend.
    vec2 at = (pixel - pictures.xy) * pictures.zw;
    vec2 shift = bend * pictures.zw;
    vec3 red = texture(sharp, at + shift * (1.0 - rim.w)).rgb;
    vec3 green = texture(sharp, at + shift).rgb;
    vec3 blue = texture(sharp, at + shift * (1.0 + rim.w)).rgb;
    vec3 bent = order.x > 0.5 ? vec3(red.b, green.g, blue.r) : vec3(red.r, green.g, blue.b);
    bent = clamp(mix(vec3(dot(bent, vec3(0.2126, 0.7152, 0.0722))), bent, light.w), 0.0, 1.0);
    bent = mix(bent, tint.rgb, tint.a);

    vec3 flat_middle = texture(middle, at).rgb;
    if (order.y > 0.5)
        flat_middle = flat_middle.bgr;
    // A rim that never gives way is a lens across the whole bar; smoothstep with equal edges is undefined.
    vec3 glass = shape.y >= 1.0 ? bent : mix(bent, flat_middle, smoothstep(shape.y, 1.0, x));

    // Light from the top left: a faint shadow inside the rim on the far side, a fine line of light along
    // the edge (weaker on the far side), then the edge itself.
    float lit = dot(normal, TO_LIGHT);
    glass *= 1.0 - light.z * max(-lit, 0.0) * u * u;
    float shine = 1.0 - smoothstep(light.x, light.x + light.y, inward);
    glass = mix(glass, highlight.rgb, highlight.a * shine * (max(lit, 0.0) + 0.45 * max(-lit, 0.0)));
    glass = mix(glass, border.rgb, border.a * clamp(light.x - inward + 0.5, 0.0, 1.0));

    colour = vec4(order.z > 0.5 ? glass.bgr : glass, 1.0);
}
