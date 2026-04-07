module top_example (
);

  mod_a u_a (
    .clk(sys_clk),
    .cfg(cfg[2:0]),
    .mid(mid), /* .a(a) */
    .data_out(data_out),
    // .line_comment_test (line_comment_test),
    /* .multi_line_comment_test (multi_line_comment_test) */
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
