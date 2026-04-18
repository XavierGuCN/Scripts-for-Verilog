module top_force_output_example (
    input [2:0] cfg,
    input clk,
    input data_in,
    output data_out,
    output flag,
    output [7:0] mid,
    input sys_clk
);

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
