module top_incremental_example (
  input cfg, // kept as-is even though the connected port is [2:0]
  output [7:0] mid, // manually promoted top output
  input stale_port
);

  wire data_out;

  mod_a u_a (
    .clk(sys_clk),
    .cfg(cfg[2:0]),
    .mid(mid),
    .data_out(data_out)
  );

  mod_b u_b (
    .clk(clk),
    .mid(mid),
    .data_in(data_in)
  );

  mod_c u_c (
    .cfg(cfg),
    .flag(flag)
  );

endmodule
