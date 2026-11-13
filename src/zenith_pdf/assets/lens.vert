#version 440

// One rectangle over the whole target. `corner` runs from (0, 0) at the bar's top left to (1, 1).

layout(location = 0) in vec2 corner;
layout(location = 0) out vec2 pixel;

layout(std140, binding = 0) uniform Lens {
    vec4 bar;
    vec4 pictures;
    vec4 rim;
    vec4 shape;
    vec4 tint;
    vec4 highlight;
    vec4 border;
    vec4 light;
    vec4 order;
};

void main()
{
    pixel = corner * bar.xy;
    // shape.w is +1 where the target's first row is the top of clip space, -1 where it is the bottom:
    // either way the bar's top row is the first row read back.
    gl_Position = vec4(corner.x * 2.0 - 1.0, (1.0 - corner.y * 2.0) * shape.w, 0.0, 1.0);
}
