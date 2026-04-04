module top_example (
);

  mod_a u_a (
    .clk(clk),
    .cfg(cfg),
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
